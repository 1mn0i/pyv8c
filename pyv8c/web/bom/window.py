"""Window and the named-properties prototype it inherits.

Cross-origin access to another window goes through :mod:`dom.window_proxy`,
which :meth:`Window._view` imports on first use.
"""
import pyv8
from pyv8 import AttributeSpec, IdlSpec

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
from ...foundation.errors import LegacyPropertyError, SecurityError
from ...foundation.urls import origin_tuple, serialize_origin
from ..dom.custom_elements import CustomElementRegistry
from ..dom.events import EventTarget
from ..fetch.request import Request
from .accessories import (
    CrashReportContext,
    External,
    LaunchQueue,
    Scheduler,
    SpeechSynthesis,
    StyleMedia,
    Viewport,
)
from .crypto import Crypto
from .media import MediaQueryList
from .navigation import Navigation
from .trusted_types import TrustedTypePolicyFactory
from .visual_viewport import VisualViewport
from .performance import Performance
from .scheduling import _SchedulingMixin
from .utils import deny_cross_origin, is_secure_context, window_attribute, window_method

__all__ = ["BarProp", "Window", "WindowProperties"]

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
        constructor_behavior=0,
        has_constructor=0,
        to_string_tag="WindowProperties",
        named_properties_object=True,
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




@interface('BarProp', None)
class BarProp:
    """One of a window's bars: only ``visible`` is observable, and always true."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="visible", getter="get_visible", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="BarProp",
        has_constructor=1,
    )

    def __init__(self, name="", visible=True, **_):
        self._name, self._visible = str(name), bool(visible)

    def get_visible(self):
        return self._visible


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


#: The window's own bar properties, one name each.
BAR_NAMES = ("locationbar", "menubar", "toolbar", "statusbar", "scrollbars", "personalbar")


def _singleton_values(window):
    """The ``[SameObject]`` objects a page reads off the window itself."""
    def bar(name):
        return lambda: BarProp(name)

    return LazyValueStore({
        "performance": Performance,
        "crypto": lambda: Crypto(is_secure_context(window.location.get_href())),
        "customElements": lambda: CustomElementRegistry(services=window._services),
        "visualViewport": lambda: VisualViewport(window=window),
        "viewport": Viewport,
        "external": External,
        "crashReport": CrashReportContext,
        "launchQueue": LaunchQueue,
        "speechSynthesis": SpeechSynthesis,
        "styleMedia": lambda: StyleMedia(window),
        "scheduler": lambda: Scheduler(services=window._services),
        "navigation": lambda: Navigation(window=window),
        "trustedTypes": TrustedTypePolicyFactory,
        **{name: bar(name) for name in BAR_NAMES},
    })


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
            window_attribute("onmessage", "get_onmessage", "set_onmessage", cross_origin=True),
            window_attribute("document", "get_document", cross_origin=True),
            window_attribute("navigator", "get_navigator", cross_origin=True),
            window_attribute("clientInformation", "get_clientInformation", cross_origin=True),
            window_attribute("screen", "get_screen", cross_origin=True),
            window_attribute("localStorage", "get_localStorage", cross_origin=True),
            window_attribute("sessionStorage", "get_sessionStorage", cross_origin=True),
            window_attribute("history", "get_history", cross_origin=True),
            window_attribute("indexedDB", "get_indexedDB", cross_origin=True),
            window_attribute("innerWidth", "get_innerWidth", cross_origin=True),
            window_attribute("innerHeight", "get_innerHeight", cross_origin=True),
            window_attribute("outerWidth", "get_outerWidth", cross_origin=True),
            window_attribute("outerHeight", "get_outerHeight", cross_origin=True),
            window_attribute("orientation", "get_orientation", cross_origin=True),
            window_attribute("devicePixelRatio", "get_devicePixelRatio", cross_origin=True),
            window_attribute("name", "get_name", "set_name", cross_origin=True),
            # Values a page reads about the window itself.
            window_attribute("origin", "get_origin", cross_origin=True),
            window_attribute("scrollX", "get_scrollX", cross_origin=True),
            window_attribute("scrollY", "get_scrollY", cross_origin=True),
            window_attribute("pageXOffset", "get_pageXOffset", cross_origin=True),
            window_attribute("pageYOffset", "get_pageYOffset", cross_origin=True),
            window_attribute("screenX", "get_screenX", cross_origin=True),
            window_attribute("screenY", "get_screenY", cross_origin=True),
            window_attribute("screenLeft", "get_screenLeft", cross_origin=True),
            window_attribute("screenTop", "get_screenTop", cross_origin=True),
            window_attribute("isSecureContext", "get_isSecureContext", cross_origin=True),
            window_attribute("crossOriginIsolated", "get_crossOriginIsolated", cross_origin=True),
            window_attribute("originAgentCluster", "get_originAgentCluster", cross_origin=True),
            window_attribute("credentialless", "get_credentialless", cross_origin=True),
            window_attribute("status", "get_status", "set_status", cross_origin=True),
            window_attribute("frameElement", "get_frameElement", cross_origin=True),
            window_attribute("performance", "get_performance", cross_origin=True),
            window_attribute("crypto", "get_crypto", cross_origin=True),
            window_attribute("customElements", "get_customElements", cross_origin=True),
            window_attribute("visualViewport", "get_visualViewport", cross_origin=True),
            window_attribute("viewport", "get_viewport", cross_origin=True),
            window_attribute("external", "get_external", cross_origin=True),
            window_attribute("styleMedia", "get_styleMedia", cross_origin=True),
            window_attribute("crashReport", "get_crashReport", cross_origin=True),
            window_attribute("launchQueue", "get_launchQueue", cross_origin=True),
            window_attribute("speechSynthesis", "get_speechSynthesis", cross_origin=True),
            window_attribute("scheduler", "get_scheduler", cross_origin=True),
            window_attribute("navigation", "get_navigation", cross_origin=True),
            window_attribute("trustedTypes", "get_trustedTypes", cross_origin=True),
            window_attribute("fence", "get_fence", cross_origin=True),
            # Declared, and answered as absent, exactly as Chrome presents them.
            window_attribute("event", "get_event", cross_origin=True),
            window_attribute("caches", "get_caches", cross_origin=True),
            window_attribute("cookieStore", "get_cookieStore", cross_origin=True),
            window_attribute("crossOriginEmbedderPolicy", "get_crossOriginEmbedderPolicy",
                             cross_origin=True),
            window_attribute("defaultStatus", "get_defaultStatus", cross_origin=True),
            window_attribute("defaultstatus", "get_defaultstatus", cross_origin=True),
            window_attribute("documentPictureInPicture", "get_documentPictureInPicture",
                             cross_origin=True),
            window_attribute("printing", "get_printing", cross_origin=True),
            window_attribute("sharedStorage", "get_sharedStorage", cross_origin=True),
            window_attribute("subApps", "get_subApps", cross_origin=True),
            window_attribute("testOriginTrialGlobalAttribute",
                             "get_testOriginTrialGlobalAttribute", cross_origin=True),
            window_attribute("locationbar", "get_locationbar", cross_origin=True),
            window_attribute("menubar", "get_menubar", cross_origin=True),
            window_attribute("toolbar", "get_toolbar", cross_origin=True),
            window_attribute("statusbar", "get_statusbar", cross_origin=True),
            window_attribute("scrollbars", "get_scrollbars", cross_origin=True),
            window_attribute("personalbar", "get_personalbar", cross_origin=True),
        ),
        methods=(
            window_method("setTimeout", "fn_setTimeout", 1, cross_origin=True),
            window_method("clearTimeout", "fn_clearTimeout", 1, cross_origin=True),
            window_method("setInterval", "fn_setInterval", 1, cross_origin=True),
            window_method("clearInterval", "fn_clearInterval", 1, cross_origin=True),
            window_method("queueMicrotask", "fn_queueMicrotask", 1, cross_origin=True),
            window_method("requestAnimationFrame", "fn_requestAnimationFrame", 1, cross_origin=True),
            window_method("cancelAnimationFrame", "fn_cancelAnimationFrame", 1, cross_origin=True),
            window_method("fetch", "fn_fetch", 1, cross_origin=True),
            window_method("close", "fn_close"),
            window_method("focus", "fn_focus"),
            window_method("blur", "fn_blur"),
            window_method("postMessage", "fn_postMessage", 1),
            window_method("webkitRequestFileSystem", "fn_webkitRequestFileSystem", 3, cross_origin=True),
            window_method("matchMedia", "fn_matchMedia", 1, cross_origin=True),
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
        # No ``index_query``: the native definer would then refuse every define.
        index_set="__v8_index_set__",
        index_del="__v8_index_del__",
        index_enum="__v8_index_enum__",
    )
    # Ordinary members use Web IDL bindings; dynamic names live on
    # WindowProperties, not a generic Python interceptor on Window.  The
    # baseline's named handlers are still filled as placeholders.
    _context_origins = {}
    def __init__(self, services=None, feature_runtime=None, **kw):
        super().__init__(services=services, **kw); self._services = services
        self._origin = getattr(services, "_origin", ("opaque", object()))
        self._proxies = {}
        #: Set for a window a frame owns; the element is what survives navigation.
        self._frame_element = None
        environment = getattr(getattr(services, "realm_services", None), "environment", None)
        viewport = BrowserEnvironment.from_dict(environment).viewport
        if feature_runtime is None:
            raise RuntimeError("Window requires the Browser FeatureRuntime")
        self._features = _WindowFeatureValues(feature_runtime, self)
        self._window_values = _singleton_values(self)
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
    def get_parent(self): return self._view(self._parent_target())
    def get_top(self): return self._view(self._top_target())

    def _parent_target(self):
        parent = self._services.parent_window()
        return self if parent is None else parent

    def _top_target(self):
        top = self._services.top_window()
        return self if top is None else top

    def _view(self, target):
        if target is self:
            return self
        # One proxy per browsing context, not per Window: a navigation replaces
        # the Window while the reference a page holds stays the same object.
        frame = getattr(target, "_frame_element", None)
        key = frame if frame is not None else target._services
        proxy = self._proxies.get(key)
        if proxy is None:
            from .window_proxy import WindowProxy
            proxy = self._proxies[key] = WindowProxy(self, target)
        return proxy

    def __v8_index_get__(self, index):
        frames = self._frame_windows()
        if 0 <= index < len(frames):
            return self._view(frames[index])
        return None

    def __v8_index_set__(self, index, value):
        """A slot holds a live window: nothing may be assigned over it."""
        raise LegacyPropertyError.indexed(
            "set", index, "Window", "Indexed property setter is not supported."
        )

    def __v8_index_del__(self, index):
        """A frame slot refuses deletion; a slot holding nothing is already gone."""
        return not 0 <= index < len(self._frame_windows())

    def __v8_index_enum__(self):
        """The child frames, in the order their slots are indexed."""
        return len(self._frame_windows())

    def _frame_windows(self):
        return tuple(node._child_services.window
                     for node in self._services.document_services.document.fn_querySelectorAll("iframe")
                     if node._child_services is not None)

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
    def set_location(self, value): self.location.set_href(value)
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
    def get_name(self):
        """The name a page gave this window; empty until it sets one."""
        return str(getattr(self, "_window_name", "") or "")
    def set_name(self, value):
        self._require_same_origin()
        self._window_name = str(value)
        self._publish_name()
    def _publish_name(self):
        """Assign the name over the one a fresh global object already carries."""
        self._services.realm_services.window_property("set", "name", self.get_name())
    def get_orientation(self):
        """Chromium's deprecated alias for the screen orientation angle.

        A desktop window is never rotated, so the reported angle -- and this
        alias with it -- is 0.
        """
        self._require_same_origin()
        return 0
    # -- what a page reads about the window itself -----------------------------
    def _window_value(self, name):
        """The one object this window hands out for a ``[SameObject]`` member."""
        return self._window_values.value(name)

    def get_origin(self):
        """The current document's origin."""
        self._require_same_origin()
        return self.location.get_origin()

    def get_scrollX(self):
        """How far the view has scrolled: nothing here scrolls one."""
        self._require_same_origin()
        return 0

    def get_scrollY(self): self._require_same_origin(); return 0
    def get_pageXOffset(self): return self.get_scrollX()
    def get_pageYOffset(self): return self.get_scrollY()

    def get_screenX(self):
        """Where the window sits on the screen: the top-left corner here."""
        self._require_same_origin()
        return 0

    def get_screenY(self): self._require_same_origin(); return 0
    def get_screenLeft(self): return self.get_screenX()
    def get_screenTop(self): return self.get_screenY()

    def get_isSecureContext(self):
        """Whether the document's origin is one a browser treats as trustworthy."""
        self._require_same_origin()
        return is_secure_context(self.location.get_href())

    def get_crossOriginIsolated(self):
        """Whether the document is cross-origin isolated: no COOP/COEP is set."""
        self._require_same_origin()
        return False

    def get_originAgentCluster(self): self._require_same_origin(); return False
    def get_credentialless(self): self._require_same_origin(); return False

    def get_status(self):
        """The window's status bar text, which starts empty and only a page sets."""
        self._require_same_origin()
        return getattr(self, "_status", "")

    def set_status(self, value):
        self._require_same_origin()
        self._status = dom_string(value)

    def get_frameElement(self):
        """The frame a child window lives in; a top-level window has none."""
        self._require_same_origin()
        parent = self._parent_target()
        if parent is self or parent._origin != self._accessor_origin():
            return pyv8.Null
        document = parent._services.document_services.document
        return next((frame for frame in document.fn_querySelectorAll("iframe")
                     if frame._child_services is not None
                     and frame._child_services.window is self), pyv8.Null)

    def get_performance(self):
        self._require_same_origin()
        return self._window_value("performance")

    def get_crypto(self):
        self._require_same_origin()
        return self._window_value("crypto")

    def get_customElements(self):
        self._require_same_origin()
        return self._window_value("customElements")

    def get_visualViewport(self):
        self._require_same_origin()
        return self._window_value("visualViewport")

    # -- the smaller accessory objects ----------------------------------------
    def get_viewport(self): self._require_same_origin(); return self._window_value("viewport")
    def get_external(self): self._require_same_origin(); return self._window_value("external")
    def get_crashReport(self): self._require_same_origin(); return self._window_value("crashReport")
    def get_launchQueue(self): self._require_same_origin(); return self._window_value("launchQueue")
    def get_speechSynthesis(self): self._require_same_origin(); return self._window_value("speechSynthesis")

    def get_styleMedia(self):
        self._require_same_origin()
        return self._window_value("styleMedia")

    def get_scheduler(self):
        self._require_same_origin()
        return self._window_value("scheduler")

    def get_navigation(self):
        self._require_same_origin()
        return self._window_value("navigation")

    def get_trustedTypes(self):
        self._require_same_origin()
        return self._window_value("trustedTypes")

    def get_fence(self):
        """An unfenced page has no fence, which Chrome reports as null."""
        self._require_same_origin()
        return pyv8.Null

    #: Chrome knows these names and answers undefined -- they gate on a secure
    #: context, or are gone -- so the value is the same and the log is quiet.
    def get_event(self): self._require_same_origin(); return pyv8.Undefined
    def get_caches(self): self._require_same_origin(); return pyv8.Undefined
    def get_cookieStore(self): self._require_same_origin(); return pyv8.Undefined
    def get_crossOriginEmbedderPolicy(self): self._require_same_origin(); return pyv8.Undefined
    def get_defaultStatus(self): self._require_same_origin(); return pyv8.Undefined
    def get_defaultstatus(self): self._require_same_origin(); return pyv8.Undefined
    def get_documentPictureInPicture(self): self._require_same_origin(); return pyv8.Undefined
    def get_printing(self): self._require_same_origin(); return pyv8.Undefined
    def get_sharedStorage(self): self._require_same_origin(); return pyv8.Undefined
    def get_subApps(self): self._require_same_origin(); return pyv8.Undefined
    def get_testOriginTrialGlobalAttribute(self): self._require_same_origin(); return pyv8.Undefined

    def get_locationbar(self): self._require_same_origin(); return self._window_value("locationbar")
    def get_menubar(self): self._require_same_origin(); return self._window_value("menubar")
    def get_toolbar(self): self._require_same_origin(); return self._window_value("toolbar")
    def get_statusbar(self): self._require_same_origin(); return self._window_value("statusbar")
    def get_scrollbars(self): self._require_same_origin(); return self._window_value("scrollbars")
    def get_personalbar(self): self._require_same_origin(); return self._window_value("personalbar")

    def fn_matchMedia(self, query=MISSING, *_extra):
        """The query list for *query*, answered against this window's viewport."""
        if query is MISSING:
            raise missing_error('Window', 'matchMedia', 1, 0, constructor=False)
        self._require_same_origin()
        return MediaQueryList(dom_string(query), self.innerWidth, self.innerHeight)
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
    
    def fn_close(self, *_extra): return None
    def fn_focus(self, *_extra): return None
    def fn_blur(self, *_extra): return None

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
