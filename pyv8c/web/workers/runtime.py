"""Worker thread and message-loop implementation."""

import threading

from ...foundation.executor import RealmExecutor
from ...foundation.lifecycle import ResourceScope
from ..fetch.clone import structured_clone
from ..fetch.events import MessageEvent


class WorkerRuntime:
    def __init__(self, owner, script_url):
        self.owner = owner
        self.services = owner._services
        self.script_url = str(script_url)
        self._stop = threading.Event()
        self._lifecycle_scope = ResourceScope()
        self.executor = RealmExecutor(name="pyv8c-worker")
        self._realm = None
        self._context = None
        self._scope = None
        self._devtools = None
        self.debug_target = None
        # Start asynchronously and return immediately.  ``new Worker()`` runs
        # on the page owner thread while it is inside the V8 constructor
        # callback; blocking there until the worker realm is fully initialised
        # lets that realm contend for the same V8 lock / GIL the owner still
        # holds, which deadlocks as soon as a second realm is also live.
        # Worker messages are still ordered correctly: ``_run`` was posted
        # first, so every later ``_dispatch_message`` runs after the scope
        # exists (or is dropped while ``_stop`` is set after a failed start).
        self.executor.post(self._run)

    def _run(self):
        try:
            config = self.services.worker_services.worker_configuration()
            source = self.services.resource_services.resolve_worker_source(self.script_url)
            from .api import DedicatedWorkerGlobalScope
            self._scope = DedicatedWorkerGlobalScope(runtime=self, environment=config.environment)
            self._realm = self.services.worker_services.create_worker_realm(config)
            self._context = self._realm.initialize(self._scope)
            devtools = config.devtools
            self._devtools = devtools
            if devtools is not None:
                devtools.register_worker(self, config.debug_parent)
                if self.debug_target is not None:
                    self.debug_target.before_script()
            if source:
                try:
                    self._context.exec_js(source, filename=self.script_url)
                finally:
                    if self.debug_target is not None:
                        self.debug_target.after_script()
        except BaseException as exc:
            self._report_error(exc)
            self._stop.set()
            self._cleanup_owner()
            self._lifecycle_scope.close()
            self.executor.close()

    def _dispatch_message(self, value):
        if self._stop.is_set() or self._scope is None:
            return
        try:
            self._scope.fn_dispatchEvent(MessageEvent("message", {
                "data": value, "origin": self.services.realm_services.url,
            }))
        except BaseException as exc:
            self._report_error(exc)

    def _cleanup_owner(self):
        devtools = self._devtools
        if devtools is not None:
            devtools.unregister(self)
        if self._realm is not None:
            self._realm.close()
            self._realm = None
        self._context = None
        self._scope = None
        self.services.task_services.post_task(self.owner._finish_lifecycle)

    def _report_error(self, exc):
        self.services.task_services.post_task(lambda: self.owner._receive_error(exc))

    def post_from_parent(self, value, transfer=()):
        if not self._stop.is_set():
            self.executor.post(self._dispatch_message, structured_clone(value, transfer))

    def post_to_parent(self, value, transfer=()):
        value = structured_clone(value, transfer, context=self._context)
        self.services.task_services.post_task(lambda: self.owner._receive_message(value))

    def stop(self):
        if self._lifecycle_scope.begin_close() is None:
            return
        self._stop.set()
        target = self.debug_target
        if target is not None:
            target.wake_waiting_owner()
        try:
            try:
                self.executor.call(self._cleanup_owner, timeout=5)
            except RuntimeError:
                # The worker thread failed to start and already cleaned up and
                # closed its executor before this terminate arrived.
                pass
        finally:
            self.executor.close()
            self._lifecycle_scope.close()


__all__ = ["WorkerRuntime"]
