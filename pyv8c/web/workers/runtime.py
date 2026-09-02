"""Worker thread and message-loop implementation."""

import queue
import threading

from ..fetch.events import MessageEvent
from .clone import structured_clone


class WorkerRuntime:
    def __init__(self, owner, script_url):
        self.owner = owner
        self.page = owner._page
        self.script_url = str(script_url)
        self._queue = queue.Queue()
        self._debugger_queue = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pyv8c-worker", daemon=True)
        self._realm = None
        self._context = None
        self._scope = None
        self.debug_target = None
        self._thread.start()
        self._ready.wait(5)

    def _run(self):
        try:
            config = self.page.worker_configuration()
            source = self.page.resolve_worker_source(self.script_url)
            from .api import DedicatedWorkerGlobalScope
            self._scope = DedicatedWorkerGlobalScope(runtime=self, environment=config["environment"])
            self._realm = self.page.create_worker_realm(config)
            self._context = self._realm.initialize(self._scope)
            devtools = config["devtools"]
            if devtools is not None:
                devtools.register_worker(self)
                if self.debug_target is not None:
                    self.debug_target._create_debugger()
                    self.debug_target.wait_for_startup()
            if source:
                self._context.exec_js(source, filename=self.script_url)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    debugger_message, target = self._debugger_queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if debugger_message is None:
                        target._create_debugger()
                    elif debugger_message == "__close__":
                        del target
                    elif target.debugger is not None:
                        target.debugger.send(debugger_message.encode("utf-8"))
                try:
                    value = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if value is None:
                    break
                try:
                    self._scope.fn_dispatchEvent(MessageEvent("message", {
                        "data": value, "origin": self.page.url,
                    }))
                except BaseException as exc:
                    self._report_error(exc)
        except BaseException as exc:
            self._report_error(exc)
            self._ready.set()
        finally:
            devtools = self.page.devtools_server
            if devtools is not None:
                devtools.unregister(self)
            if self._realm is not None:
                self._realm.close()
                self._realm = None
            self._context = None
            self._scope = None
            self.page.post_task(self.owner._finish_lifecycle)

    def _report_error(self, exc):
        self.page.post_task(lambda: self.owner._receive_error(exc))

    def post_from_parent(self, value):
        if not self._stop.is_set():
            self._queue.put(structured_clone(value))

    def enqueue_debugger(self, message, target):
        if not self._stop.is_set():
            self._debugger_queue.put((message, target))

    def enqueue_debugger_init(self, target):
        if not self._stop.is_set():
            self._debugger_queue.put((None, target))

    def enqueue_debugger_close(self, debugger):
        if not self._stop.is_set():
            self._debugger_queue.put(("__close__", debugger))

    def post_to_parent(self, value):
        try:
            value = structured_clone(value, context=self._context)
        except BaseException as exc:
            self._report_error(exc)
            return
        self.page.post_task(lambda: self.owner._receive_message(value))

    def stop(self):
        self._stop.set()
        self._queue.put(None)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)


__all__ = ["WorkerRuntime"]
