"""Worker Web API and global scope types."""
from urllib.parse import urljoin

from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import FlagLocation

from ...engine.features import FeatureImplementation, LazyValueStore, interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    dom_string,
    enum_value,
    event_handler,
    is_nullish,
    missing_error,
    usv_string,
)
from ...foundation.errors import InvalidStateError
from ..bom.scheduling import _SchedulingMixin
from ..dom.events import EventTarget
from ..fetch.clone import message_transfer
from ..fetch.events import ErrorEvent, MessageEvent


def _worker_values(scope):
    """The worker global's own objects, built on first read.

    Navigator belongs to the navigator Feature, so it is asked of the realm
    rather than imported.
    """
    def navigator():
        cls = scope._services.realm_services.materialize_interface("Navigator")
        return cls(environment=scope._environment, worker=True, services=scope._services)

    return LazyValueStore({"navigator": navigator})


@interface('WorkerGlobalScope', 'EventTarget')
class WorkerGlobalScope(_SchedulingMixin, EventTarget):
    __v8_idl_spec__ = IdlSpec(
        global_this=True,
        attributes=(
            AttributeSpec(name="self", getter="get_self", side_effect=1),
            AttributeSpec(name="navigator", getter="get_navigator", side_effect=1),
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
        ),
        methods=(
            MethodSpec(name="postMessage", callback="fn_postMessage", length=1),
            MethodSpec(name="close", callback="fn_close"),
            # A worker global schedules like a window, on its own task queue.
            MethodSpec(name="setTimeout", callback="fn_setTimeout", length=1),
            MethodSpec(name="clearTimeout", callback="fn_clearTimeout", length=0),
            MethodSpec(name="setInterval", callback="fn_setInterval", length=1),
            MethodSpec(name="clearInterval", callback="fn_clearInterval", length=0),
            MethodSpec(name="queueMicrotask", callback="fn_queueMicrotask", length=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    #: Interface name the scheduling methods report in argument errors.
    _scheduling_interface = "WorkerGlobalScope"

    def __init__(self, runtime=None, environment=None, services=None, **kw):
        super().__init__(services=services, **kw)
        self._runtime = runtime
        self._environment = environment
        self._values = _worker_values(self)

    def get_self(self): return self
    def get_navigator(self): return self._values.value("navigator")
    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onmessage', value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onerror', value)
    def fn_postMessage(self, value=MISSING, options=None, *_extra):
        if value is MISSING:
            raise missing_error('WorkerGlobalScope', 'postMessage', 1, 0, constructor=False)
        transfer = message_transfer(options)
        self._runtime.post_to_parent(value, transfer)
    def fn_close(self, *_extra):
        self._runtime.stop()


@interface('DedicatedWorkerGlobalScope', 'WorkerGlobalScope')
class DedicatedWorkerGlobalScope(WorkerGlobalScope):
    __v8_idl_spec__ = IdlSpec(
        methods=(
            # Frame requests live on the dedicated scope, as the baseline declares.
            MethodSpec(name="requestAnimationFrame", callback="fn_requestAnimationFrame", length=1, location=FlagLocation.kInstance),
            MethodSpec(name="cancelAnimationFrame", callback="fn_cancelAnimationFrame", length=1, location=FlagLocation.kInstance),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )


@interface('Worker', 'EventTarget')
class Worker(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
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

    def __init__(self, script_url=MISSING, options=None, *_extra, _realm=None, **kw):
        if script_url is MISSING:
            raise missing_error('Worker', 'constructor', 1, 0, constructor=True)
        script_url = usv_string(script_url)
        options = dictionary(options, name="WorkerOptions", prefix="Failed to construct 'Worker': ")
        self._credentials = enum_value(options.get("credentials", "same-origin"), ("omit", "same-origin", "include"))
        self._worker_name = dom_string(options.get("name", ""))
        self._worker_type = enum_value(options.get("type", "classic"), ("classic", "module"))
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
    def set_onmessage(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onmessage', value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onerror', value)

    def fn_postMessage(self, value=MISSING, options=None, *_extra):
        if value is MISSING:
            raise missing_error('Worker', 'postMessage', 1, 0, constructor=False)
        transfer = message_transfer(options)
        if self._terminated:
            raise InvalidStateError("Worker has been terminated")
        self._runtime.post_from_parent(value, transfer)

    def _receive_message(self, value):
        if not self._terminated:
            self.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self._services.realm_services.url}))

    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error), "error": error}))

    def _receive_uncaught(self, message, filename, line, column):
        """Report an exception V8 raised inside the worker's own realm.

        The error value cannot cross realms, so the Worker object receives the
        text and position V8 captured.
        """
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {
                "message": message,
                "filename": filename,
                "lineno": line,
                "colno": column,
            }))

    def _finish_lifecycle(self):
        if getattr(self, "_lifecycle_finished", False): return
        self._lifecycle_finished = True
        self._services.task_services.complete_worker()
        self._services.worker_services.discard_worker(self)

    def fn_terminate(self, *_extra):
        if not self._terminated:
            self._runtime.stop()
            self._terminated = True
            self._finish_lifecycle()


__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope"]


FEATURE = FeatureImplementation(
    {
        "WorkerGlobalScope": WorkerGlobalScope,
        "DedicatedWorkerGlobalScope": DedicatedWorkerGlobalScope,
        "Worker": Worker,
    },
)
