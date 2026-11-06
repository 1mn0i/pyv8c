"""Targets and independent V8 Inspector sessions."""

import json
import threading
import uuid

from .. import _pyv8
from .scheduler import Mailbox


class NativeSession(_pyv8.Debugger):
    def __init__(self, session):
        self.session = session
        super().__init__(session.target.host.context)

    def handle(self, message):
        self.session.receive(json.loads(message))

    def on_ready(self):
        if self.session.closed or self.session.generation != self.session.router.generation:
            return
        self.session.ready = True
        self.session.target.mark_ready()

    def run_loop(self):
        self.session.target.mailbox.run_pause()

    def quit_loop(self):
        self.session.target.mailbox.quit_pause()


class Session:
    def __init__(self, router, target, session_id, generation, parent=None, automatic=False):
        self.router = router
        self.target = target
        self.session_id = session_id
        self.generation = generation
        self.parent = parent
        self.automatic = automatic
        self.native = None
        self.closed = False
        self.ready = False
        self.enabled = False
        self.auto_attach = False
        self.wait_children = False
        self.target.sessions.add(self)

    def ensure_native(self):
        if self.native is None:
            self.native = NativeSession(self)
        return self.native

    def dispatch(self, request):
        if self.closed or self.target.closed:
            return
        self.ensure_native().send(json.dumps(request, separators=(",", ":")).encode())

    def receive(self, payload):
        method = payload.get("method")
        if method == "Debugger.paused":
            self.target.mailbox.prepare_pause()
            if self.target.pause_armed:
                self.target.pause_consumed = True
        if not self.closed:
            self.router.receive(self, payload)

    def dispose(self):
        if self.native is not None:
            self.native.cancel_pause_on_next_statement()
            self.native.disconnect()
            self.native = None

    def close(self):
        if self.closed:
            return None
        self.closed = True
        self.target.sessions.discard(self)
        self.router.requests.cancel(self)
        if not self.target.sessions:
            self.target.release()
        elif not any(entry.ready for entry in self.target.sessions):
            self.target.ready.clear()
        def release_pause():
            if self.native is not None:
                self.native.resume()
        self.target.submit(release_pause)
        return self.target.mailbox.submit(self.target, self.dispose, during_pause=False)


class Target:
    def __init__(self, host, options, parent=None):
        self.host = host
        self.options = options
        self.parent = parent
        self.target_id = uuid.uuid4().hex
        self.children = set()
        if parent is not None:
            parent.children.add(self)
        self.mailbox = Mailbox.for_isolate(host.context.isolate_id)
        self.sessions = set()
        self.closed = False
        self.waiting = options.wait_for_debugger if parent is None else False
        self.started = False
        self.ready = threading.Event()
        self.disconnected = False
        self.pause_consumed = False
        self.pause_armed = False
        self.diagnostics = []
        self.wake_dispatch = self._wake_dispatch

    def _wake_dispatch(self):
        self.mailbox.wake()
        self.host.executor.post(self.mailbox.drain, self)

    def descriptor(self):
        return {
            "targetId": self.target_id, "type": self.host.kind,
            "title": self.host.url(), "url": self.host.url(),
            "attached": bool(self.sessions), "canAccessOpener": False,
            "browserContextId": "pyv8",
        }

    def rebind_context(self, context):
        native = next((entry.native for entry in self.sessions if entry.native is not None), None)
        if native is not None:
            native.rebind_context(context)
        self.host.context = context

    def submit(self, callback):
        future = self.mailbox.submit(self, callback)
        return future

    def mark_ready(self):
        self.disconnected = False
        self.ready.set()
        self.waiting = False
        self.mailbox.wake()

    def release(self):
        self.waiting = False
        self.disconnected = True
        self.ready.clear()
        self.mailbox.wake()

    def wait_for_ready(self, timeout=None, *, pump=False):
        predicate = lambda: self.ready.is_set() or self.closed or self.disconnected
        if pump:
            self.mailbox.wait(self, predicate, timeout)
        else:
            with self.mailbox.condition:
                self.mailbox.condition.wait_for(predicate, timeout)
        return self.ready.is_set() and not self.closed

    def wait_for_startup(self):
        if self.closed or not self.waiting:
            return
        suspended = self.host.context._suspend_deadlines()
        try:
            if not self.mailbox.wait(self, lambda: not self.waiting or self.closed, self.options.startup_timeout):
                raise TimeoutError("DevTools startup timed out before the first script")
        finally:
            self.host.context._resume_deadlines(suspended)

    def before_script(self):
        self.wait_for_startup()
        self.started = True
        self.mailbox.drain(self)
        if self.closed or self.pause_consumed or not self.options.pause_on_start:
            return
        session = next((entry for entry in self.sessions if entry.ready and entry.enabled), None)
        if session is not None:
            self.pause_armed = True
            session.ensure_native().schedule_pause_on_next_statement("pyv8.start")

    def wake_waiting_owner(self):
        self.release()
        for child in tuple(self.children):
            child.wake_waiting_owner()
        for session in tuple(self.sessions):
            def resume(entry=session):
                if entry.native is not None:
                    entry.native.resume()
            self.submit(resume)

    def after_script(self):
        self.pause_armed = False
        for session in tuple(self.sessions):
            if session.native is not None:
                session.native.cancel_pause_on_next_statement()

    def close(self):
        if self.closed:
            return []
        self.closed = True
        self.wake_waiting_owner()
        if self.parent is not None:
            self.parent.children.discard(self)
        return [future for session in tuple(self.sessions) if (future := session.close()) is not None]
