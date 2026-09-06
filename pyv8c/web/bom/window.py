"""Window, its named-properties prototype, and realm-aware WindowProxy."""

import pyv8
from pyv8.flag import FlagExposed, FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck, CallbackType, SideEffectType
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.environment import BrowserEnvironment
from ...foundation.errors import SecurityError
from ..dom.model import EventTarget
from ..fetch.request import Request
from .api import History, Location, Screen, Storage
from .navigator import Navigator

__all__ = ["Window", "WindowProperties", "WindowProxy"]


@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=0)
@interface("WindowProperties", "EventTarget", constructible=False, tag="WindowProperties", exposed_in=())
class WindowProperties(EventTarget):
    """The named-properties object between Window and EventTarget prototypes.

    Native WindowProperties callbacks run against the *owning* Window, even
    when its prototype is borrowed by a different realm. They intentionally
    use a separate callback family from Window's own named interceptors.
    """

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


def _window_attribute(name, getter, setter=None, *, cross_origin=False, flags=0, side_effect=SideEffectType.kHasSideEffect):
    check = FlagCrossOriginCheck.kCheck if cross_origin else FlagCrossOriginCheck.kDoNotCheck
    return (name, getter, setter, CallbackType.kPy, FlagLocation.kInstance,
            flags, FlagReceiverCheck.kCheck, check, check, side_effect)


def _window_method(name, callback, length=0, *, cross_origin=False, side_effect=SideEffectType.kHasSideEffect):
    check = FlagCrossOriginCheck.kCheck if cross_origin else FlagCrossOriginCheck.kDoNotCheck
    return (name, callback, length, CallbackType.kPy, FlagLocation.kInstance,
            0, FlagReceiverCheck.kCheck, check, side_effect)


@interface("Window", "WindowProperties", constructible=False, tag="Window", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Window(WindowProperties):
    __v8_global_this__ = True
    # Ordinary members use Web IDL bindings; dynamic names live on
    # WindowProperties, not a generic Python interceptor on Window.
    __v8_disable_named_handlers__ = True
    _context_origins = {}
    __v8_attribute__ = (
        _window_attribute("window", "get_window"), _window_attribute("self", "get_self"),
        _window_attribute("parent", "get_parent"), _window_attribute("top", "get_top"),
        _window_attribute("frames", "get_frames"), _window_attribute("length", "get_length"),
        _window_attribute("closed", "get_closed"), _window_attribute("opener", "get_opener"),
        _window_attribute("location", "get_location", "set_location"),
        _window_attribute("onmessage", "get_onmessage", "set_onmessage", cross_origin=True),
        _window_attribute("document", "get_document", cross_origin=True),
        _window_attribute("navigator", "get_navigator", cross_origin=True),
        _window_attribute("screen", "get_screen", cross_origin=True),
        _window_attribute("localStorage", "get_localStorage", cross_origin=True),
        _window_attribute("sessionStorage", "get_sessionStorage", cross_origin=True),
        _window_attribute("history", "get_history", cross_origin=True),
        _window_attribute("innerWidth", "get_innerWidth", cross_origin=True),
        _window_attribute("innerHeight", "get_innerHeight", cross_origin=True),
        _window_attribute("outerWidth", "get_outerWidth", cross_origin=True),
        _window_attribute("outerHeight", "get_outerHeight", cross_origin=True),
        _window_attribute("devicePixelRatio", "get_devicePixelRatio", cross_origin=True),
    )
    __v8_method__ = (
        _window_method("setTimeout", "fn_setTimeout", 1, cross_origin=True),
        _window_method("clearTimeout", "fn_clearTimeout", 1, cross_origin=True),
        _window_method("setInterval", "fn_setInterval", 1, cross_origin=True),
        _window_method("clearInterval", "fn_clearInterval", 1, cross_origin=True),
        _window_method("queueMicrotask", "fn_queueMicrotask", 1, cross_origin=True),
        _window_method("requestAnimationFrame", "fn_requestAnimationFrame", 1, cross_origin=True),
        _window_method("fetch", "fn_fetch", 1, cross_origin=True),
        _window_method("close", "fn_close"), _window_method("focus", "fn_focus"),
        _window_method("blur", "fn_blur"),
        _window_method("postMessage", "fn_postMessage", 1),
    )
    def __init__(self, services=None, **kw):
        super().__init__(services=services, **kw); self._services = services
        self._origin = getattr(services, "_origin", ("opaque", object()))
        self._parent_window = self
        self._top_window = self
        self._proxies = {}
        environment = getattr(getattr(services, "realm_services", None), "environment", None)
        viewport = BrowserEnvironment.from_value(environment).viewport
        self.location = Location(services=services); self.navigator = Navigator(services=services, environment=environment); self.screen = Screen(environment=environment)
        self.localStorage, self.sessionStorage = Storage(), Storage()
        self.innerWidth, self.innerHeight = viewport.width, viewport.height
        self.outerWidth, self.outerHeight = viewport.width, viewport.height
        self.devicePixelRatio = viewport.device_scale_factor
        self.history = History(services=services)
    def __v8_cross_origin_check__(self, accessor):
        return accessor is self or getattr(accessor, "_origin", None) == self._origin

    def _cross_origin_proxy(self):
        entry = self._context_origins.get(id(pyv8.current_context()))
        if entry is None:
            raise SecurityError("The accessing Window is not available")
        return entry[0]._view(self)

    def __v8_cross_origin_name_get__(self, name):
        proxy = self._cross_origin_proxy()
        if name in WindowProxy._safe_names:
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
        return list(WindowProxy._safe_names)

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
            raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")

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
    def set_onmessage(self, value): self._require_same_origin(); self._set_handler("onmessage", value)
    def get_location(self): return self.location
    def set_location(self, value): self.location.set_href(value)
    def get_navigator(self): self._require_same_origin(); return self.navigator
    def get_screen(self): self._require_same_origin(); return self.screen
    def get_localStorage(self): self._require_same_origin(); return self.localStorage
    def get_sessionStorage(self): self._require_same_origin(); return self.sessionStorage
    def get_history(self): self._require_same_origin(); return self.history
    def get_innerWidth(self): self._require_same_origin(); return self.innerWidth
    def get_innerHeight(self): self._require_same_origin(); return self.innerHeight
    def get_outerWidth(self): self._require_same_origin(); return self.outerWidth
    def get_outerHeight(self): self._require_same_origin(); return self.outerHeight
    def get_devicePixelRatio(self): self._require_same_origin(); return self.devicePixelRatio
    def fn_setTimeout(self, callback, delay=0): return self._services.task_services.schedule(callback, float(delay) / 1000.0, repeat=False)
    def fn_clearTimeout(self, ident): self._services.task_services.cancel(int(ident))
    def fn_setInterval(self, callback, delay=0): return self._services.task_services.schedule(callback, float(delay) / 1000.0, repeat=True)
    def fn_clearInterval(self, ident): self._services.task_services.cancel(int(ident))
    def fn_queueMicrotask(self, callback): self._services.task_services.microtasks.append(callback)
    def fn_requestAnimationFrame(self, callback): return self._services.task_services.schedule(callback, 0, repeat=False)
    def fn_fetch(self, request, options=None):
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            req = request if isinstance(request, Request) else Request(request, options, services=self._services)
        except BaseException as exc:
            self._services.realm_services.post_task(lambda exc=exc: reject(self._services.realm_services.js_error_value(exc)))
            return promise
        self._services.resource_services.load_resource_async(req.get_url(), {
            "method": req.get_method(), "headers": req.get_headers(), "body": req._body,
            "timeout": req._timeout, "credentials": req._credentials, "mode": req._mode, "redirect": req._redirect,
            "resource_type": "fetch",
        }, lambda response, error: reject(self._services.realm_services.js_error_value(error)) if error else resolve(response))
        return promise
    def fn_close(self):
        return None
    def fn_focus(self):
        return None
    def fn_blur(self):
        return None
    def fn_postMessage(self, message, target_origin="/", transfer=()):
        self._post_message(self, message, target_origin, transfer)

    def _post_message(self, source, message, target_origin="/", transfer=()):
        from ..network.backend import origin_tuple
        from ..workers.clone import structured_clone
        from ..fetch.events import MessageEvent
        from ...foundation.errors import V8SyntaxError

        # Null is an empty WindowPostMessageOptions dictionary. Explicit
        # undefined is defaulted by the reviewed binding signature.
        if target_origin is None or target_origin is pyv8.Null or target_origin is pyv8.Undefined:
            target_origin = {}
        if isinstance(target_origin, dict):
            transfer = target_origin.get("transfer", ())
            target_origin = target_origin.get("targetOrigin", "/")
        target_origin = str(target_origin)
        if target_origin == "/":
            expected_origin = source._origin
        elif target_origin == "*":
            expected_origin = None
        else:
            expected_origin = origin_tuple(target_origin)
            if expected_origin[0] == "opaque":
                raise V8SyntaxError("Invalid target origin in postMessage")
        data = structured_clone(message, transfer)
        origin = source._origin
        if origin[0] == "opaque":
            serialized = "null"
        else:
            scheme, host, port = origin
            host = "[" + host + "]" if ":" in host else host
            default = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}.get(scheme)
            serialized = scheme + "://" + host + (":" + str(port) if port != default else "")

        def deliver():
            if self.get_closed() or (expected_origin is not None and expected_origin != self._origin):
                return
            self._services.realm_services.materialize_interface("MessageEvent")
            event = MessageEvent("message", {"data": data, "origin": serialized,
                                           "source": self._view(source), "isTrusted": True})
            self.fn_dispatchEvent(event)
        self._services.realm_services.post_task(deliver)


class _CrossOriginLocation:
    """The restricted Location object exposed by a cross-origin WindowProxy.

    Chrome permits assigning ``other.location`` (and ``location.href``), but
    does not expose the target URL or its parsed components to the caller.
    """

    __v8_attribute__ = js_attributes(
        ("href", "get_href", "set_href", 0, 0),
        ("origin", "get_origin", None, 0, 0),
        ("protocol", "get_protocol", None, 0, 0),
        ("host", "get_host", None, 0, 0),
        ("hostname", "get_hostname", None, 0, 0),
        ("port", "get_port", None, 0, 0),
        ("pathname", "get_pathname", None, 0, 0),
        ("search", "get_search", None, 0, 0),
        ("hash", "get_hash", None, 0, 0),
    )
    __v8_method__ = js_methods(("replace", "fn_replace", 1, 0))

    def __init__(self, location):
        self._location = location

    @staticmethod
    def _deny():
        raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")

    def get_href(self): self._deny()
    def get_origin(self): self._deny()
    def get_protocol(self): self._deny()
    def get_host(self): self._deny()
    def get_hostname(self): self._deny()
    def get_port(self): self._deny()
    def get_pathname(self): self._deny()
    def get_search(self): self._deny()
    def get_hash(self): self._deny()
    def set_href(self, value): return self._location.set_href(value)
    def fn_replace(self, value): return self._location.set_href(value)

    def __v8_name_get__(self, name):
        if name in ("href", "origin", "protocol", "host", "hostname", "port", "pathname", "search", "hash", "replace"):
            return None
        self._deny()

    def __v8_name_set__(self, name, value):
        if name == "href":
            self.set_href(value)
            return True
        self._deny()

    def __v8_name_del__(self, name): self._deny()


class WindowProxy:
    """Stable parent-realm facade for a cross-origin iframe Window.

    A child ``Window`` is a global object owned by its own V8 Context and must
    never be handed directly to another Context.  This small native-backed
    facade keeps identity stable while implementing the browser's safe
    cross-origin surface.
    """

    __v8_attribute__ = js_attributes(
        ("window", "get_window", None, 0, 1),
        ("self", "get_self", None, 0, 1),
        ("top", "get_top", None, 0, 1),
        ("parent", "get_parent", None, 0, 1),
        ("opener", "get_opener", None, 0, 1),
        ("frames", "get_frames", None, 0, 1),
        ("length", "get_length", None, 0, 1),
        ("closed", "get_closed", None, 0, 1),
        ("location", "get_location", "set_location", 0, 0),
    )
    __v8_method__ = js_methods(
        ("close", "fn_close", 0, 0),
        ("focus", "fn_focus", 0, 0),
        ("blur", "fn_blur", 0, 0),
        ("postMessage", "fn_postMessage", 2, 0),
    )

    def __init__(self, parent_window, child_window):
        self._parent_window = parent_window
        self._child_window = child_window
        self._location = _CrossOriginLocation(child_window.location)

    _safe_names = frozenset(("window", "self", "top", "parent", "opener", "frames",
                             "length", "closed", "location", "close", "focus", "blur", "postMessage"))

    @staticmethod
    def _deny():
        raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")

    def __v8_name_get__(self, name):
        if name in self._safe_names:
            return None
        if self._same_origin():
            if isinstance(name, str):
                getter = getattr(self._child_window, "get_" + name, None)
                if getter is not None:
                    return getter()
                callback = getattr(self._child_window, "fn_" + name, None)
                if callback is not None:
                    return callback
            return self._child_window._services.realm_services.window_property("get", name)
        self._deny()

    def __v8_name_set__(self, name, value):
        if name == "location":
            self.set_location(value)
            return True
        if self._same_origin():
            setter = getattr(self._child_window, "set_" + str(name), None)
            if setter is not None:
                setter(value)
            else:
                self._child_window._services.realm_services.window_property("set", name, value)
            return True
        self._deny()

    def __v8_name_del__(self, name):
        if self._same_origin():
            self._child_window._services.realm_services.window_property("delete", name)
            return True
        self._deny()

    def __v8_name_query__(self, name):
        if name in self._safe_names:
            return pyv8.V8Attribute.kNone
        if self._same_origin():
            return self._child_window._services.realm_services.window_property("query", name)
        self._deny()

    def __v8_name_enum__(self): return list(self._safe_names)

    def _same_origin(self):
        return self._accessor()._origin == self._child_window._origin

    def _accessor(self):
        return Window._accessor_window(self._parent_window)

    def __v8_index_get__(self, index):
        frames = self._child_window._frame_windows()
        if 0 <= index < len(frames):
            return self._accessor()._view(frames[index])
        self._deny()

    def __v8_index_set__(self, index, value): self._deny()
    def __v8_index_del__(self, index): self._deny()

    def get_window(self): return self
    def get_self(self): return self
    def get_frames(self): return self
    def get_top(self): return self._accessor()._view(self._child_window._top_window)
    def get_parent(self): return self._accessor()._view(self._child_window._parent_window)
    def get_opener(self): return pyv8.Null
    def get_length(self): return self._child_window.get_length()
    def get_closed(self): return self._child_window.get_closed()
    def get_location(self): return self._child_window.location if self._same_origin() else self._location
    def set_location(self, value): return self._child_window.location.set_href(value)
    def fn_close(self): return self._child_window.fn_close()
    def fn_focus(self): return self._child_window.fn_focus()
    def fn_blur(self): return self._child_window.fn_blur()
    def fn_postMessage(self, message, target_origin="/", transfer=()):
        return self._child_window._post_message(self._accessor(), message, target_origin, transfer)
