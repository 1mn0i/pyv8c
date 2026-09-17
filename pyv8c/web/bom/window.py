"""Window and the named-properties prototype it inherits.

Cross-origin access to another window goes through :mod:`dom.window_proxy`,
which :meth:`Window._view` imports on first use.
"""
import pyv8
from pyv8 import IdlSpec

from ...engine.features import LazyValueStore, interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    dom_string,
    event_handler,
    is_nullish,
    missing_error,
    usv_string,
)
from ...foundation.environment import BrowserEnvironment
from ...foundation.errors import SecurityError
from ...foundation.urls import origin_tuple, serialize_origin
from ..dom.events import EventTarget
from ..fetch.request import Request
from .scheduling import _SchedulingMixin
from .utils import deny_cross_origin, window_attribute, window_method

__all__ = ["Window", "WindowProperties"]

#: Names :class:`~dom.window_proxy.WindowProxy` exposes to a cross-origin
#: caller.  ``Window`` enumerates the same set when a proxy asks it for its own
#: keys, so the table lives here rather than in the proxy module.
_PROXY_SAFE_NAMES = frozenset((
    "window", "self", "top", "parent", "opener", "frames", "length", "closed",
    "location", "close", "focus", "blur", "postMessage",
))


@interface('WindowProperties', 'EventTarget')
class WindowProperties(EventTarget):
    """The named-properties object between Window and EventTarget prototypes.

    Native WindowProperties callbacks run against the *owning* Window, even
    when its prototype is borrowed by a different realm. They intentionally
    use a separate callback family from Window's own named interceptors.
    """
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, has_constructor=0, to_string_tag="WindowProperties"
    )

    _NAMED_TAGS = frozenset(("embed", "form", "img", "object"))

    def _named_elements(self, name):
        document = self._services.document_services.document
        return [node for node in document.fn_querySelectorAll("*")
                if node.attributes.get("id") == name or (
                    node.localName in self._NAMED_TAGS and node.attributes.get("name") == name)]

    def __v8_property_name_get__(self, name):
        if not isinstance(name, str) or not name:
            return None
        self._require_same_origin()
        document = self._services.document_services.document
        # Child browsing-context names take precedence over matching elements.
        for frame in document.fn_querySelectorAll("iframe"):
            if frame.attributes.get("name") == name and frame._child_services is not None:
                return Window._accessor_window(self)._view(frame._child_services.window)
        matches = self._named_elements(name)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        from ..dom.collections import HTMLCollection
        return HTMLCollection(owner=document, getter=lambda: self._named_elements(name))

    def __v8_property_name_query__(self, name):
        return 0 if self.__v8_property_name_get__(name) is not None else None

    def __v8_property_name_enum__(self):
        # Named-properties objects have no own keys, despite synthesized
        # [[GetOwnProperty]] descriptors for currently supported names.
        return []




class _WindowFeatureValues(LazyValueStore):
    """The Window's feature slots, built by their Feature on first access.

    A Window value is declared by ``FeatureSpec.window_slots`` and constructed
    by that Feature's ``window_values`` factory, so this store only routes a
    slot name to :meth:`FeatureRuntime.create_window_value` and caches the
    result: construction knowledge stays in the Feature, identity and lifetime
    stay per Window.
    """

    __slots__ = ("_runtime", "_window")

    def __init__(self, runtime, window):
        super().__init__()
        self._runtime, self._window = runtime, window

    def _create_value(self, slot):
        return self._runtime.create_window_value(slot, self._window)


@interface('Window', 'WindowProperties')
class Window(_SchedulingMixin, WindowProperties):
    __v8_idl_spec__ = IdlSpec(
        global_this=True,
        attributes=(
            window_attribute("window", "get_window"),
            window_attribute("self", "get_self"),
            window_attribute("parent", "get_parent"),
            window_attribute("top", "get_top"),
            window_attribute("frames", "get_frames"),
            window_attribute("length", "get_length"),
            window_attribute("closed", "get_closed"),
            window_attribute("opener", "get_opener"),
            window_attribute("location", "get_location", "set_location"),
            window_attribute(
                "onmessage", "get_onmessage", "set_onmessage", cross_origin=True
            ),
            window_attribute("document", "get_document", cross_origin=True),
            window_attribute("navigator", "get_navigator", cross_origin=True),
            window_attribute("clientInformation", "get_clientInformation", cross_origin=True),
            window_attribute("screen", "get_screen", cross_origin=True),
            window_attribute("localStorage", "get_localStorage", cross_origin=True),
            window_attribute(
                "sessionStorage", "get_sessionStorage", cross_origin=True
            ),
            window_attribute("history", "get_history", cross_origin=True),
            window_attribute("indexedDB", "get_indexedDB", cross_origin=True),
            window_attribute("innerWidth", "get_innerWidth", cross_origin=True),
            window_attribute("innerHeight", "get_innerHeight", cross_origin=True),
            window_attribute("outerWidth", "get_outerWidth", cross_origin=True),
            window_attribute("outerHeight", "get_outerHeight", cross_origin=True),
            window_attribute("orientation", "get_orientation", cross_origin=True),
            window_attribute(
                "devicePixelRatio", "get_devicePixelRatio", cross_origin=True
            ),
        ),
        methods=(
            window_method("setTimeout", "fn_setTimeout", 1, cross_origin=True),
            window_method("clearTimeout", "fn_clearTimeout", 1, cross_origin=True),
            window_method("setInterval", "fn_setInterval", 1, cross_origin=True),
            window_method("clearInterval", "fn_clearInterval", 1, cross_origin=True),
            window_method("queueMicrotask", "fn_queueMicrotask", 1, cross_origin=True),
            window_method(
                "requestAnimationFrame",
                "fn_requestAnimationFrame",
                1,
                cross_origin=True,
            ),
            window_method(
                "cancelAnimationFrame", "fn_cancelAnimationFrame", 1, cross_origin=True
            ),
            window_method("fetch", "fn_fetch", 1, cross_origin=True),
            window_method("close", "fn_close"),
            window_method("focus", "fn_focus"),
            window_method("blur", "fn_blur"),
            window_method("postMessage", "fn_postMessage", 1),
            window_method("webkitRequestFileSystem", "fn_webkitRequestFileSystem", 3, cross_origin=True),
            window_method("getComputedStyle", "fn_getComputedStyle", 1, cross_origin=True),
        ),
        constructor_behavior=0,
        to_string_tag="Window",
        has_constructor=1,
        cross_origin_check="__v8_cross_origin_check__",
        cross_origin_name_get="__v8_cross_origin_name_get__",
        cross_origin_name_set="__v8_cross_origin_name_set__",
        cross_origin_name_query="__v8_cross_origin_name_query__",
        cross_origin_name_del="__v8_cross_origin_name_del__",
        cross_origin_name_enum="__v8_cross_origin_name_enum__",
        cross_origin_index_get="__v8_cross_origin_index_get__",
        cross_origin_index_set="__v8_cross_origin_index_set__",
        cross_origin_index_del="__v8_cross_origin_index_del__",
        index_get="__v8_index_get__",
    )
    # Ordinary members use Web IDL bindings; dynamic names live on
    # WindowProperties, not a generic Python interceptor on Window.
    _context_origins = {}
    def __init__(self, services=None, feature_runtime=None, **kw):
        super().__init__(services=services, **kw); self._services = services
        self._origin = getattr(services, "_origin", ("opaque", object()))
        self._parent_window = self
        self._top_window = self
        self._proxies = {}
        environment = getattr(getattr(services, "realm_services", None), "environment", None)
        viewport = BrowserEnvironment.from_value(environment).viewport
        if feature_runtime is None:
            raise RuntimeError("Window requires the Browser FeatureRuntime")
        self._features = _WindowFeatureValues(feature_runtime, self)
        self.location = self._features.value("location")
        self.innerWidth, self.innerHeight = viewport.width, viewport.height
        self.outerWidth, self.outerHeight = viewport.width, viewport.height
        self.devicePixelRatio = viewport.device_scale_factor

    @property
    def navigator(self):
        return self._features.value("navigator")

    @property
    def screen(self):
        return self._features.value("screen")

    @property
    def localStorage(self):
        return self._features.value("localStorage")

    @property
    def sessionStorage(self):
        return self._features.value("sessionStorage")

    @property
    def history(self):
        return self._features.value("history")

    @property
    def indexedDB(self):
        return self._features.value("indexedDB")

    def sync_history_url(self, url):
        """Re-point the current history entry at a committed document URL.

        A navigation replaces the current entry's URL here (no entry is pushed),
        so this keeps the entry table coherent for later ``back``/``forward`` and
        fragment navigation.  ``history`` is created lazily, so a page that never
        reads it does not materialize one just because it navigated.
        """
        if "history" in self._features.cached_names:
            self.history.replace_same_document(str(url))
    def __v8_cross_origin_check__(self, accessor):
        return accessor is self or getattr(accessor, "_origin", None) == self._origin

    def _cross_origin_proxy(self):
        entry = self._context_origins.get(id(pyv8.current_context()))
        if entry is None:
            raise SecurityError("The accessing Window is not available")
        return entry[0]._view(self)

    def __v8_cross_origin_name_get__(self, name):
        proxy = self._cross_origin_proxy()
        if name in _PROXY_SAFE_NAMES:
            getter = getattr(proxy, "get_" + name, None)
            return getter() if getter else getattr(proxy, "fn_" + name)
        return proxy.__v8_name_get__(name)

    def __v8_cross_origin_name_set__(self, name, value):
        return self._cross_origin_proxy().__v8_name_set__(name, value)

    def __v8_cross_origin_name_query__(self, name):
        return self._cross_origin_proxy().__v8_name_query__(name)

    def __v8_cross_origin_name_del__(self, name):
        return self._cross_origin_proxy().__v8_name_del__(name)

    def __v8_cross_origin_name_enum__(self):
        return list(_PROXY_SAFE_NAMES)

    def __v8_cross_origin_index_get__(self, index):
        return self._cross_origin_proxy().__v8_index_get__(index)

    def __v8_cross_origin_index_set__(self, index, value):
        return self._cross_origin_proxy().__v8_index_set__(index, value)

    def __v8_cross_origin_index_del__(self, index):
        return self._cross_origin_proxy().__v8_index_del__(index)

    @classmethod
    def _bind_context(cls, context, window):
        cls._context_origins[id(context)] = (window, window._origin)

    @classmethod
    def _unbind_context(cls, context):
        cls._context_origins.pop(id(context), None)

    @classmethod
    def _accessor_window(cls, fallback=None):
        entry = cls._context_origins.get(id(pyv8.current_context()))
        return entry[0] if entry is not None else fallback

    def _accessor_origin(self):
        current = pyv8.current_context()
        entry = self._context_origins.get(id(current))
        return entry[0]._origin if entry is not None else self._origin

    def _same_origin(self):
        return self._accessor_origin() == self._origin

    def _require_same_origin(self):
        if not self._same_origin():
            deny_cross_origin()

    def get_window(self): return self
    def get_self(self): return self
    def get_parent(self):
        return self._view(self._parent_window)

    def get_top(self):
        return self._view(self._top_window)

    def _view(self, target):
        if target is self:
            return self
        if target not in self._proxies:
            from .window_proxy import WindowProxy
            self._proxies[target] = WindowProxy(self, target)
        return self._proxies[target]

    def __v8_index_get__(self, index):
        frames = self._frame_windows()
        if 0 <= index < len(frames):
            return self._view(frames[index])
        return None

    def _frame_windows(self):
        return tuple(node._child_services.window
                     for node in self._services.document_services.document.fn_querySelectorAll("iframe")
                     if node._child_services is not None)

    def _set_frame_parent(self, parent_window):
        self._parent_window = parent_window if parent_window is not None else self
        self._top_window = parent_window._top_window if parent_window is not None else self
    def get_document(self): self._require_same_origin(); return self._services.document_services.document
    def get_frames(self): return self
    def get_length(self): return len(self._frame_windows())
    def get_closed(self): return bool(getattr(self._services, "closed", False))
    def get_opener(self): return pyv8.Null
    def get_onmessage(self): self._require_same_origin(); return self._get_handler("onmessage")
    def set_onmessage(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._require_same_origin()
        self._set_handler('onmessage', value)
    def get_location(self): return self.location
    def set_location(self, value):
        self.location.set_href(value)
    def get_navigator(self): self._require_same_origin(); return self.navigator
    def get_clientInformation(self): self._require_same_origin(); return self.navigator
    def get_screen(self): self._require_same_origin(); return self.screen
    def get_localStorage(self): self._require_same_origin(); return self.localStorage
    def get_sessionStorage(self): self._require_same_origin(); return self.sessionStorage
    def get_history(self): self._require_same_origin(); return self.history
    def get_indexedDB(self): self._require_same_origin(); return self.indexedDB
    def get_innerWidth(self): self._require_same_origin(); return self.innerWidth
    def get_innerHeight(self): self._require_same_origin(); return self.innerHeight
    def get_outerWidth(self): self._require_same_origin(); return self.outerWidth
    def get_outerHeight(self): self._require_same_origin(); return self.outerHeight
    def get_devicePixelRatio(self): self._require_same_origin(); return self.devicePixelRatio
    def get_orientation(self):
        """Chromium's deprecated alias for the screen orientation angle.

        A desktop window is never rotated, so the reported angle -- and this
        alias with it -- is 0.
        """
        self._require_same_origin()
        return 0
    def fn_fetch(self, request=MISSING, options=None, *_extra):
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            if request is MISSING:
                raise missing_error('Window', 'fetch', 1, 0)
            if not isinstance(request, Request):
                request = usv_string(request)
            options = dictionary(options, name="RequestInit", prefix="Failed to execute 'fetch' on 'Window': ")
            req = Request(request, options, services=self._services)
        except BaseException as exc:
            self._services.realm_services.post_task(lambda exc=exc: reject(self._services.realm_services.js_error_value(exc)))
            return promise
        self._services.resource_services.load_resource_async(req.get_url(), {
            "method": req.get_method(), "headers": req.get_headers(), "body": req._body,
            "timeout": req._timeout, "credentials": req._credentials, "mode": req._mode, "redirect": req._redirect,
            "resource_type": "fetch",
        }, lambda response, error: reject(self._services.realm_services.js_error_value(error)) if error else resolve(response))
        return promise
    def fn_close(self, *_extra):
        return None
    def fn_focus(self, *_extra):
        return None
    def fn_blur(self, *_extra):
        return None
    def fn_postMessage(self, message=MISSING, target_origin='/', transfer=MISSING, *_extra):
        if message is MISSING:
            raise missing_error('Window', 'postMessage', 1, 0, constructor=False)
        if target_origin is pyv8.Undefined:
            target_origin = '/'
        from ...foundation.arguments import sequence
        if transfer is not MISSING:
            target_origin = dom_string(target_origin)
            transfer = sequence(transfer)
        else:
            transfer = ()
        self._post_message(self, message, target_origin, transfer)

    def fn_webkitRequestFileSystem(self, type=MISSING, size=MISSING, success=MISSING, error=pyv8.Undefined, *_extra):
        if type is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 0, constructor=False)
        if size is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 1, constructor=False)
        if success is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 2, constructor=False)
        self._require_same_origin()
        # The API is an Optional Feature: loading it commits its interface group
        # to this realm before the first FileSystem instance is handed back.
        self._services.realm_services.materialize_interface("DOMFileSystem")
        from .filesystem import request_file_system
        return request_file_system(self, type, size, success, error)
    def fn_getComputedStyle(self, element=MISSING, pseudo=pyv8.Undefined, *_extra):
        if element is MISSING:
            raise missing_error("Window", "getComputedStyle", 1, 0, constructor=False)
        from ..dom.html_elements import Element
        if not isinstance(element, Element):
            raise TypeError("The provided value is not an Element")
        from ..dom.layout import computed_style_object
        return computed_style_object(element)

    def _post_message(self, source, message, target_origin="/", transfer=()):
        from ...foundation.errors import V8SyntaxError
        from ..fetch.clone import structured_clone
        from ..fetch.events import MessageEvent

        # Null is an empty WindowPostMessageOptions dictionary.
        if target_origin is None or target_origin is pyv8.Null or target_origin is pyv8.Undefined:
            target_origin = {}
        if isinstance(target_origin, (dict, pyv8.JSObject)):
            from ...foundation.arguments import sequence
            options = dictionary(target_origin)
            target_origin = dom_string(options.get("targetOrigin", "/"))
            transfer = sequence(options.get("transfer", ()))
        target_origin = dom_string(target_origin)
        if target_origin == "/":
            expected_origin = source._origin
        elif target_origin == "*":
            expected_origin = None
        else:
            expected_origin = origin_tuple(target_origin)
            if expected_origin[0] == "opaque":
                raise V8SyntaxError("Invalid target origin in postMessage")
        data = structured_clone(message, transfer)
        serialized = serialize_origin(source._origin)

        def deliver():
            if self.get_closed() or (expected_origin is not None and expected_origin != self._origin):
                return
            self._services.realm_services.materialize_interface("MessageEvent")
            event = MessageEvent("message", {"data": data, "origin": serialized,
                                           "source": self._view(source), "isTrusted": True})
            self.fn_dispatchEvent(event)
        self._services.realm_services.post_task(deliver)
