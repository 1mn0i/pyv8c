"""Worker Web API and global scope types."""

from urllib.parse import urljoin

from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.errors import InvalidStateError
from ..dom.events import EventTarget
from ..fetch.events import ErrorEvent, MessageEvent


@interface("WorkerGlobalScope", "EventTarget", constructible=False, exposed_in=("Worker",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class WorkerGlobalScope(EventTarget):
    __v8_global_this__ = True
    __v8_attribute__ = js_attributes(
        ("self", "get_self", None, 0, 1), ("navigator", "get_navigator", None, 0, 1),
        ("onmessage", "get_onmessage", "set_onmessage", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
    )
    __v8_method__ = js_methods(("postMessage", "fn_postMessage", 1, 0), ("close", "fn_close", 0, 0))

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


@interface("DedicatedWorkerGlobalScope", "WorkerGlobalScope", constructible=False, exposed_in=("Worker",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class DedicatedWorkerGlobalScope(WorkerGlobalScope):
    pass


@interface("Worker", "EventTarget", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Worker(EventTarget):
    __v8_attribute__ = js_attributes(
        ("onmessage", "get_onmessage", "set_onmessage", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
    )
    __v8_method__ = js_methods(("postMessage", "fn_postMessage", 1, 0), ("terminate", "fn_terminate", 0, 0))

    def __init__(self, script_url="", _realm=None, **kw):
        services = _realm
        super().__init__(services=services, **kw)
        self._services = services
        self._terminated = False
        self._lifecycle_finished = False
        self._script_url = urljoin(services.url, str(script_url)) if services and not str(script_url).startswith("blob:") else str(script_url)
        if services is not None:
            services._track_worker(self)
            services.begin_worker()
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
            self.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self._services.url}))

    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error), "error": error}))

    def _finish_lifecycle(self):
        if getattr(self, "_lifecycle_finished", False): return
        self._lifecycle_finished = True
        self._services.complete_worker()
        self._services._discard_worker(self)

    def fn_terminate(self):
        if not self._terminated:
            self._terminated = True
            self._runtime.stop()
            self._finish_lifecycle()


__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope"]
