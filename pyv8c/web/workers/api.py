"""Worker Web API and global scope types."""
from urllib.parse import urljoin, urlsplit

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
from ...foundation.urls import origin_string
from ..bom.scheduling import _SchedulingMixin
from ..bom.utils import is_secure_context
from ..dom.events import EventTarget
from ..fetch.clone import message_transfer
from ..fetch.events import ErrorEvent, MessageEvent


def _worker_values(scope):
    """The worker global's own objects, built on first read.

    Navigator, crypto and trustedTypes are siblings in the feature runtime, so the
    realm is asked for them rather than importing them. The security of the worker's
    own URL decides whether its crypto carries randomUUID, and a blob URL is as
    trustworthy as the origin it carries.
    """
    def interface(name):
        return scope._services.realm_services.materialize_interface(name)

    def navigator():
        return interface("Navigator")(environment=scope._environment, worker=True,
                                      services=scope._services)

    return LazyValueStore({
        "navigator": navigator,
        "crypto": lambda: interface("Crypto")(
            secure=is_secure_context(scope.script_url()), services=scope._services),
        "trustedTypes": lambda: interface("TrustedTypePolicyFactory")(),
        "performance": lambda: interface("Performance")(services=scope._services),
        "indexedDB": lambda: interface("IDBFactory")(services=scope._services),
        "location": lambda: interface("WorkerLocation")(url=scope.script_url()),
        "caches": lambda: interface("CacheStorage")(services=scope._services),
    })


@interface('WorkerLocation', None)
class WorkerLocation:
    """Where the worker's script came from, as the worker itself sees it."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="href", getter="get_href", side_effect=1),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(name="protocol", getter="get_protocol", side_effect=1),
            AttributeSpec(name="host", getter="get_host", side_effect=1),
            AttributeSpec(name="hostname", getter="get_hostname", side_effect=1),
            AttributeSpec(name="port", getter="get_port", side_effect=1),
            AttributeSpec(name="pathname", getter="get_pathname", side_effect=1),
            AttributeSpec(name="search", getter="get_search", side_effect=1),
            AttributeSpec(name="hash", getter="get_hash", side_effect=1),
        ),
        methods=(MethodSpec(name="toString", callback="fn_toString"),),
        constructor_behavior=0,
        to_string_tag="WorkerLocation",
        has_constructor=1,
    )

    def __init__(self, url="", **_):
        self._url = str(url or "")

    def _parts(self): return urlsplit(self._url)

    def get_href(self): return self._url
    def get_origin(self): return origin_string(self._url)
    def get_protocol(self): return self._parts().scheme + ":"
    def get_host(self): return self._parts().netloc
    def get_hostname(self): return self._parts().hostname or ""
    def get_port(self): return str(self._parts().port or "")
    def get_pathname(self): return self._parts().path
    def get_search(self): return ("?" + self._parts().query) if self._parts().query else ""
    def get_hash(self): return ("#" + self._parts().fragment) if self._parts().fragment else ""
    def fn_toString(self, *_extra): return self._url


@interface('WorkerGlobalScope', 'EventTarget')
class WorkerGlobalScope(_SchedulingMixin, EventTarget):
    __v8_idl_spec__ = IdlSpec(
        global_this=True,
        attributes=(
            AttributeSpec(name="self", getter="get_self", side_effect=1),
            AttributeSpec(name="navigator", getter="get_navigator", side_effect=1),
            AttributeSpec(name="crypto", getter="get_crypto", side_effect=1),
            AttributeSpec(name="trustedTypes", getter="get_trustedTypes", side_effect=1),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(name="crossOriginIsolated", getter="get_crossOriginIsolated",
                          side_effect=1),
            AttributeSpec(name="performance", getter="get_performance", side_effect=1),
            AttributeSpec(name="indexedDB", getter="get_indexedDB", side_effect=1),
            AttributeSpec(name="location", getter="get_location", side_effect=1),
            AttributeSpec(name="caches", getter="get_caches", side_effect=1),
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
        ),
        methods=(
            MethodSpec(name="postMessage", callback="fn_postMessage", length=1),
            MethodSpec(name="close", callback="fn_close"),
            # A worker fetches for itself; the steps are the window's, named after this global.
            MethodSpec(name="fetch", callback="fn_fetch", length=1),
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

    def fn_fetch(self, request=MISSING, options=None, *_extra):
        """``fetch`` for a worker global: the same steps a window runs.

        A worker's own realm services carry no resource capability, so the load goes through
        the services of the page that owns it -- which is where every other worker load goes.
        ``initiator_url`` is this worker's own script URL, so the request is attributed to the
        worker: a worker started from a ``blob:`` sends no ``Referer`` and says
        ``Sec-Fetch-Site: same-origin``, where the owner page's URL said otherwise.
        """
        from ..bom.window import fetch_like

        return fetch_like(self._services, "WorkerGlobalScope", request, options,
                          resources=getattr(self._runtime, "services", None),
                          initiator_url=self.script_url())

    def get_self(self): return self
    def get_navigator(self): return self._values.value("navigator")
    def get_crypto(self): return self._values.value("crypto")
    def get_trustedTypes(self): return self._values.value("trustedTypes")
    def get_performance(self): return self._values.value("performance")
    def get_indexedDB(self): return self._values.value("indexedDB")
    def get_location(self): return self._values.value("location")
    def get_caches(self): return self._values.value("caches")
    def get_origin(self):
        """The origin the worker was started from, which a blob URL carries."""
        return origin_string(self.script_url())
    def get_crossOriginIsolated(self):
        """A worker inherits its owner's isolation, and no document here is isolated."""
        return False
    def script_url(self):
        """The URL the worker was started from, which decides its security."""
        return str(getattr(self._runtime, "script_url", "") or "")
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

    def _receive_message(self, value, ports=()):
        if not self._terminated:
            #: A message from a worker carries no origin, and it is a real event (as in Chrome).
            self.fn_dispatchEvent(MessageEvent("message", {
                "data": value, "origin": "", "ports": ports, "isTrusted": True}))

    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"isTrusted": True, "message": str(error), "error": error}))

    def _receive_uncaught(self, message, filename, line, column):
        """Report an exception V8 raised inside the worker's own realm.

        The error value cannot cross realms, so the Worker object receives the
        text and position V8 captured.
        """
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"isTrusted": True, 
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


@interface('SharedWorkerGlobalScope', 'WorkerGlobalScope')
class SharedWorkerGlobalScope(WorkerGlobalScope):
    """A shared worker's global: the port it serves arrives as a ``connect`` event."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1,
                          location=FlagLocation.kInstance),
            AttributeSpec(name="onconnect", getter="get_onconnect", setter="set_onconnect",
                          location=FlagLocation.kInstance),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def get_name(self):
        return getattr(getattr(self._runtime, "owner", None), "_worker_name", "")
    def get_onconnect(self): return self._get_handler("onconnect")
    def set_onconnect(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onconnect', value)


@interface('SharedWorker', 'EventTarget')
class SharedWorker(EventTarget):
    """A worker an origin shares, reached through the port it hands out."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="port", getter="get_port", side_effect=1),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, script_url=MISSING, options=None, *_extra, _realm=None, **kw):
        if script_url is MISSING:
            raise missing_error('SharedWorker', 'constructor', 1, 0, constructor=True)
        script_url = usv_string(script_url)
        options = dictionary(options, name="WorkerOptions",
                             prefix="Failed to construct 'SharedWorker': ")
        self._worker_name = dom_string(options.get("name", ""))
        self._worker_type = enum_value(options.get("type", "classic"), ("classic", "module"))
        services = _realm
        super().__init__(services=services, **kw)
        self._services = services
        self._terminated = False
        self._script_url = (
            urljoin(services.realm_services.url, str(script_url))
            if services and not str(script_url).startswith("blob:") else str(script_url)
        )
        # Two loose ends: the runtime entangles them once the worker's realm exists,
        # so what the page posts meanwhile is delivered on the worker's own queue.
        from ..bom.messaging import MessagePort
        self._port = MessagePort(services=services)
        self._worker_port = MessagePort(services=services)
        if services is not None:
            services.worker_services.track_worker(self)
            # The page waits for the worker's turns, so a reply lands before idle does.
            services.task_services.begin_worker()
        from .runtime import SharedWorkerRuntime
        self._runtime = SharedWorkerRuntime(self, self._script_url)

    def get_port(self): return self._port
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onerror', value)

    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"isTrusted": True, "message": str(error), "error": error}))

    def _receive_uncaught(self, message, filename, line, column):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"isTrusted": True, 
                "message": message, "filename": filename, "lineno": line, "colno": column,
            }))

    def _finish_lifecycle(self):
        """The runtime's own report that the worker stopped."""
        if getattr(self, "_lifecycle_finished", False): return
        self._lifecycle_finished = True
        if self._services is not None:
            self._services.task_services.complete_worker()
            self._services.worker_services.discard_worker(self)

    def fn_terminate(self, *_extra):
        """What the page's own cleanup calls: the worker and its realm go away."""
        if not self._terminated:
            self._terminated = True
            self._runtime.stop()
            self._finish_lifecycle()


__all__ = ["Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope",
           "SharedWorker", "SharedWorkerGlobalScope", "WorkerLocation"]


FEATURE = FeatureImplementation(
    {
        "WorkerGlobalScope": WorkerGlobalScope,
        "DedicatedWorkerGlobalScope": DedicatedWorkerGlobalScope,
        "SharedWorkerGlobalScope": SharedWorkerGlobalScope,
        "Worker": Worker,
        "SharedWorker": SharedWorker,
        "WorkerLocation": WorkerLocation,
    },
)
