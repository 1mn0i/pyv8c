"""Worker thread and message-loop implementation."""

import threading

from ...foundation.executor import RealmExecutor
from ...foundation.lifecycle import ResourceScope
from ..fetch.events import MessageEvent
from .clone import structured_clone


class WorkerRuntime:
    def __init__(self, owner, script_url):
        self.owner = owner
        self.services = owner._services
        self.script_url = str(script_url)
        self._stop = threading.Event()
        self._lifecycle_scope = ResourceScope()
        self._ready = threading.Event()
        self.executor = RealmExecutor(name="pyv8c-worker")
        self._realm = None
        self._context = None
        self._scope = None
        self.debug_target = None
        self.executor.post(self._run)
        self._ready.wait(5)

    def _run(self):
        try:
            config = self.services.worker_configuration()
            source = self.services.resolve_worker_source(self.script_url)
            from .api import DedicatedWorkerGlobalScope
            self._scope = DedicatedWorkerGlobalScope(runtime=self, environment=config.environment)
            self._realm = self.services.create_worker_realm(config)
            self._context = self._realm.initialize(self._scope)
            devtools = config.devtools
            if devtools is not None:
                devtools.register_worker(self)
                if self.debug_target is not None:
                    self.debug_target._create_debugger()
                    self.debug_target.wait_for_startup()
            if source:
                self._context.exec_js(source, filename=self.script_url)
            self._ready.set()
        except BaseException as exc:
            self._report_error(exc)
            self._ready.set()
            self._stop.set()
            self._cleanup_owner()
            self._lifecycle_scope.close()
            self.executor.close()

    def _dispatch_message(self, value):
        if self._stop.is_set() or self._scope is None:
            return
        try:
            self._scope.fn_dispatchEvent(MessageEvent("message", {
                "data": value, "origin": self.services.url,
            }))
        except BaseException as exc:
            self._report_error(exc)

    def _cleanup_owner(self):
        devtools = self.services.devtools_server
        if devtools is not None:
            devtools.unregister(self)
        if self._realm is not None:
            self._realm.close()
            self._realm = None
        self._context = None
        self._scope = None
        self.services.post_task(self.owner._finish_lifecycle)

    def _report_error(self, exc):
        self.services.post_task(lambda: self.owner._receive_error(exc))

    def post_from_parent(self, value):
        if not self._stop.is_set():
            self.executor.post(self._dispatch_message, structured_clone(value))

    def post_to_parent(self, value):
        try:
            value = structured_clone(value, context=self._context)
        except BaseException as exc:
            self._report_error(exc)
            return
        self.services.post_task(lambda: self.owner._receive_message(value))

    def stop(self):
        if self._lifecycle_scope.begin_close() is None:
            return
        self._stop.set()
        target = self.debug_target
        if target is not None:
            release = getattr(target, "_release_startup", None)
            if callable(release):
                release()
            wake_pause = getattr(target, "_stop_pause_loop", None)
            if callable(wake_pause):
                wake_pause()
        try:
            self.executor.call(self._cleanup_owner, timeout=5)
        finally:
            self.executor.close()
            self._lifecycle_scope.close()


__all__ = ["WorkerRuntime"]
