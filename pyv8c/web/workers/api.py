"""Worker Web API and global scope types."""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

from urllib.parse import urljoin




from ...engine.registry import interface
from ...foundation.errors import InvalidStateError
from ..dom.events import EventTarget
from ..fetch.events import ErrorEvent, MessageEvent


@interface('WorkerGlobalScope', 'EventTarget', exposed_in=('Worker',))
class WorkerGlobalScope(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        global_this=True,
        attributes=(
            AttributeSpec(name="self", getter="get_self", side_effect=1),
            AttributeSpec(name="navigator", getter="get_navigator", side_effect=1),
            AttributeSpec(
                name="onmessage", getter="get_onmessage", setter="set_onmessage"
            ),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
        ),
        methods=(
            MethodSpec(name="postMessage", callback="fn_postMessage", length=1),
            MethodSpec(name="close", callback="fn_close"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, runtime=None, environment=None, **kw):
        super().__init__(services=None, **kw)
        self._runtime = runtime
        from ..bom.navigator import Navigator
        self.navigator = Navigator(environment=environment, worker=True)

    def get_self(self): return self
    def get_navigator(self): return self.navigator
    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value): self._set_handler("onmessage", value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value): self._set_handler("onerror", value)
    def fn_postMessage(self, value): self._runtime.post_to_parent(value)
    def fn_close(self): self._runtime.stop()


@interface('DedicatedWorkerGlobalScope', 'WorkerGlobalScope', exposed_in=('Worker',))
class DedicatedWorkerGlobalScope(WorkerGlobalScope):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=0, has_constructor=1)
    pass


@interface('Worker', 'EventTarget')
class Worker(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(
                name="onmessage", getter="get_onmessage", setter="set_onmessage"
            ),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
        ),
        methods=(
            MethodSpec(name="postMessage", callback="fn_postMessage", length=1),
            MethodSpec(name="terminate", callback="fn_terminate"),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, script_url="", _realm=None, **kw):
        services = _realm
        super().__init__(services=services, **kw)
        self._services = services
        self._terminated = False
        self._lifecycle_finished = False
        self._script_url = urljoin(services.realm_services.url, str(script_url)) if services and not str(script_url).startswith("blob:") else str(script_url)
        if services is not None:
            services.worker_services.track_worker(self)
            services.task_services.begin_worker()
        from .runtime import WorkerRuntime
        self._runtime = WorkerRuntime(self, self._script_url)

    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value): self._set_handler("onmessage", value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value): self._set_handler("onerror", value)

    def fn_postMessage(self, value):
        if self._terminated:
            raise InvalidStateError("Worker has been terminated")
        self._runtime.post_from_parent(value)

    def _receive_message(self, value):
        if not self._terminated:
            self.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self._services.realm_services.url}))

    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error), "error": error}))

    def _finish_lifecycle(self):
        if getattr(self, "_lifecycle_finished", False): return
        self._lifecycle_finished = True
        self._services.task_services.complete_worker()
        self._services.worker_services.discard_worker(self)

    def fn_terminate(self):
        if not self._terminated:
            self._terminated = True
            self._runtime.stop()
            self._finish_lifecycle()


__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope"]
