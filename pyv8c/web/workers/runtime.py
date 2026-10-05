"""Worker thread and message-loop implementation."""

import threading

from loguru import logger

from ...foundation.arguments import is_nullish
from ...foundation.executor import RealmExecutor
from ...foundation.lifecycle import ResourceScope
from ...foundation.rejections import RejectionTracker
from ..dom.events import PromiseRejectionEvent
from ..fetch.clone import structured_clone, transfer_ports
from ..fetch.events import ErrorEvent, MessageEvent


class WorkerRuntime:
    def __init__(self, owner, script_url):
        self.owner = owner
        self.services = owner._services
        self.script_url = str(script_url)
        self._stop = threading.Event()
        self._lifecycle_scope = ResourceScope()
        self.executor = RealmExecutor(name="pyv8c-worker")
        # V8 reports a rejection before the turn that caused it ends, so the
        # candidates wait for this realm's own executor boundary to be
        # published; see foundation.rejections.
        self.rejections = RejectionTracker(self._dispatch_rejection)
        self._realm = None
        self._context = None
        self._scope = None
        self._devtools = None
        self.debug_target = None
        # Start asynchronously (``new Worker()`` does not wait for the script):
        # ``_run`` is posted first, so every later ``_dispatch_message`` runs after
        # the scope exists, and messages stay ordered.
        self.executor.post(self._run)

    def _run(self):
        try:
            config = self.services.worker_services.worker_configuration()
            source = self.services.resource_services.resolve_worker_source(self.script_url)
            self._realm = self.services.worker_services.create_worker_realm(config)
            self._realm.script_url = self.script_url
            # The worker drives its own task queue from this thread, so timers
            # and microtasks progress while the parent page is idle.
            self.executor.attach_task_queue(self._realm.tasks)
            self.executor.attach_rejection_tracker(self.rejections)
            self._realm.tasks.set_error_sink(self._report_error)
            self._scope = self._scope_class()(
                runtime=self,
                environment=config.environment,
                services=self._realm.services,
            )
            self._context = self._realm.initialize(self._scope)
            self.executor.attach_deadline_source(self._context)
            # A worker global schedules its own work, so the failures V8 raises
            # on its own are reported from this realm, not the page's.
            self._realm.scripts.install_error_hooks(
                self._report_uncaught, self.rejections.record,
            )
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
            self._started()
        except BaseException as exc:  # a worker's failure is reported, not raised
            self._report_error(exc)
            self._stop.set()
            self._cleanup_owner()
            self._lifecycle_scope.close()
            self.executor.close()

    def _scope_class(self):
        """The global scope a worker of this kind runs its script in."""
        from .api import DedicatedWorkerGlobalScope
        return DedicatedWorkerGlobalScope

    def _started(self):
        """What a worker of this kind does once its script has run."""

    def _dispatch_message(self, value, ports=()):
        if self._stop.is_set() or self._scope is None:
            return
        try:
            self._scope.fn_dispatchEvent(MessageEvent("message", {
                "data": value, "origin": self.services.realm_services.url,
                "ports": ports,
            }))
        except BaseException as exc:  # a message error is reported, not raised
            self._report_error(exc)

    def _cleanup_owner(self):
        first_error = None
        devtools = self._devtools
        if devtools is not None:
            try:
                devtools.unregister(self)
                self._devtools = None
            except BaseException as exc:
                first_error = exc
        self.rejections.close()
        if self._realm is not None:
            try:
                self._realm.close()
                self._realm = None
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if self._realm is None:
            self._context = None
            self._scope = None
        if first_error is not None:
            raise first_error
        self.services.task_services.post_task(self.owner._finish_lifecycle)

    def _report_uncaught(self, message, source, line, column, error):
        """Report an exception V8 raised inside this worker's realm.

        The global sees it first; the parent hears through the Worker object,
        which carries text and position because a value cannot cross realms.
        """
        message = str(message)
        filename = "" if is_nullish(source) else str(source)
        line, column = int(line), int(column)
        scope = self._scope
        if scope is not None:
            try:
                scope.fn_dispatchEvent(ErrorEvent("error", {
                    "message": message,
                    "filename": filename,
                    "lineno": line,
                    "colno": column,
                    "error": error,
                }))
            except BaseException:  # reporting must not break the worker
                logger.debug("failed to report a worker error", exc_info=True)
        self.services.task_services.post_task(
            lambda: self.owner._receive_uncaught(message, filename, line, column)
        )

    def _dispatch_rejection(self, event_type, promise, reason):
        """Fire a rejection event on the worker's own global scope."""
        scope = self._scope
        if scope is None or self._stop.is_set():
            return
        try:
            scope.fn_dispatchEvent(PromiseRejectionEvent(event_type, {
                "promise": promise, "reason": reason,
            }))
        except BaseException:  # reporting must not break the worker
            logger.debug("failed to report a worker rejection", exc_info=True)

    def _report_error(self, exc):
        self.services.task_services.post_task(lambda: self.owner._receive_error(exc))

    def post_from_parent(self, value, transfer=()):
        if not self._stop.is_set():
            # The port moves out of the page now; the realm it moves into is the
            # worker's, which ``_run`` has already been posted ahead of.
            ports = transfer_ports(transfer, services=self.services, caller='Worker')
            self.executor.post(self._dispatch_from_parent,
                               (structured_clone(value), ports))

    def _dispatch_from_parent(self, payload):
        value, ports = payload
        realm = self._realm
        for port in ports:
            port._services = realm.services if realm is not None else None
        self._dispatch_message(value, ports)

    def post_to_parent(self, value, transfer=()):
        ports = transfer_ports(transfer, services=self.services, caller='WorkerGlobalScope')
        value = structured_clone(value, context=self._context)
        self.services.task_services.post_task(
            lambda: self.owner._receive_message(value, ports))

    def stop(self):
        if self.executor.closed and self._realm is None:
            return
        self._stop.set()
        target = self.debug_target
        if target is not None:
            target.wake_waiting_owner()
        try:
            self.executor.call(self._cleanup_owner, timeout=5)
        except RuntimeError:
            if not (self.executor.closed and self._realm is None):
                raise
        self.executor.close()
        self._lifecycle_scope.close()


class SharedWorkerRuntime(WorkerRuntime):
    """A shared worker: once its script has run it is handed the port it serves on."""

    def _scope_class(self):
        from .api import SharedWorkerGlobalScope
        return SharedWorkerGlobalScope

    def _started(self):
        port = getattr(self.owner, "_worker_port", None)
        if self._scope is None or port is None:
            return
        # The far end of the page's port lives here, so its messages arrive on this
        # realm's queue; the page-side port flushes whatever it was given meanwhile.
        port._services = self._realm.services
        port._entangle(self.owner._port)
        self.owner._port._entangle(port)
        self._scope.fn_dispatchEvent(MessageEvent("connect", {"ports": [port]}))


__all__ = ["WorkerRuntime", "SharedWorkerRuntime"]
