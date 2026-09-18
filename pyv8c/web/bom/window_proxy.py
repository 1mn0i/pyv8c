"""Cross-origin access to another browsing context's ``Window``.

A child ``Window`` is a global object owned by its own V8 Context, so it must
never be handed to a different Context.  Every cross-origin reference therefore
goes through :class:`WindowProxy`, a native-backed facade that keeps identity
stable while exposing only the browser's safe cross-origin surface.
``Window._view`` caches one proxy per target window and imports this module on
first use, which keeps :mod:`dom.window` out of the cycle the reverse import
would otherwise create.
"""

import v8n
from v8n import AttributeSpec, IdlSpec, MethodSpec
from v8n.flag import V8Attribute

from ...foundation.arguments import MISSING, dom_string, missing_error, usv_string
from .utils import deny_cross_origin
from .window import _PROXY_SAFE_NAMES, Window

__all__ = ["WindowProxy"]




class _CrossOriginLocation:
    """The restricted Location object exposed by a cross-origin WindowProxy.

    Chrome permits assigning ``other.location`` (and ``location.href``), but
    does not expose the target URL or its parsed components to the caller.
    """

    __v8_idl_spec__ = IdlSpec(
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_del="__v8_name_del__",
        attributes=(
            AttributeSpec(name="href", getter="get_href", setter="set_href"),
            AttributeSpec(name="origin", getter="get_origin"),
            AttributeSpec(name="protocol", getter="get_protocol"),
            AttributeSpec(name="host", getter="get_host"),
            AttributeSpec(name="hostname", getter="get_hostname"),
            AttributeSpec(name="port", getter="get_port"),
            AttributeSpec(name="pathname", getter="get_pathname"),
            AttributeSpec(name="search", getter="get_search"),
            AttributeSpec(name="hash", getter="get_hash"),
        ),
        methods=(MethodSpec(name="replace", callback="fn_replace", length=1),),
    )

    def __init__(self, location):
        self._location = location

    def get_href(self): deny_cross_origin()
    def get_origin(self): deny_cross_origin()
    def get_protocol(self): deny_cross_origin()
    def get_host(self): deny_cross_origin()
    def get_hostname(self): deny_cross_origin()
    def get_port(self): deny_cross_origin()
    def get_pathname(self): deny_cross_origin()
    def get_search(self): deny_cross_origin()
    def get_hash(self): deny_cross_origin()
    def set_href(self, value): return self._location.set_href(value)
    def fn_replace(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error("Location", "replace", 1, 0)
        return self._location.set_href(usv_string(value))

    def __v8_name_get__(self, name):
        if name in ("href", "origin", "protocol", "host", "hostname", "port", "pathname", "search", "hash", "replace"):
            return None
        deny_cross_origin()

    def __v8_name_set__(self, name, value):
        if name == "href":
            self.set_href(value)
            return True
        deny_cross_origin()

    def __v8_name_del__(self, name): deny_cross_origin()


class WindowProxy:
    """Stable parent-realm facade for a cross-origin iframe Window.

    A child ``Window`` is a global object owned by its own V8 Context and must
    never be handed directly to another Context.  This small native-backed
    facade keeps identity stable while implementing the browser's safe
    cross-origin surface.
    """

    __v8_idl_spec__ = IdlSpec(
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
        index_get="__v8_index_get__",
        index_set="__v8_index_set__",
        index_del="__v8_index_del__",
        attributes=(
            AttributeSpec(name="window", getter="get_window", side_effect=1),
            AttributeSpec(name="self", getter="get_self", side_effect=1),
            AttributeSpec(name="top", getter="get_top", side_effect=1),
            AttributeSpec(name="parent", getter="get_parent", side_effect=1),
            AttributeSpec(name="opener", getter="get_opener", side_effect=1),
            AttributeSpec(name="frames", getter="get_frames", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
            AttributeSpec(name="closed", getter="get_closed", side_effect=1),
            AttributeSpec(name="location", getter="get_location", setter="set_location"),
        ),
        methods=(
            MethodSpec(name="close", callback="fn_close"),
            MethodSpec(name="focus", callback="fn_focus"),
            MethodSpec(name="blur", callback="fn_blur"),
            MethodSpec(name="postMessage", callback="fn_postMessage", length=2),
        ),
    )

    def __init__(self, parent_window, child_window):
        self._parent_window = parent_window
        self._child_window = child_window
        self._location = _CrossOriginLocation(child_window.location)

    #: The names a cross-origin caller may read; see :mod:`dom.window`.
    _safe_names = _PROXY_SAFE_NAMES

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
        deny_cross_origin()

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
        deny_cross_origin()

    def __v8_name_del__(self, name):
        if self._same_origin():
            self._child_window._services.realm_services.window_property("delete", name)
            return True
        deny_cross_origin()

    def __v8_name_query__(self, name):
        if name in self._safe_names:
            return V8Attribute.kNone
        if self._same_origin():
            return self._child_window._services.realm_services.window_property("query", name)
        deny_cross_origin()

    def __v8_name_enum__(self): return list(self._safe_names)

    def _same_origin(self):
        return self._accessor()._origin == self._child_window._origin

    def _accessor(self):
        return Window._accessor_window(self._parent_window)

    def __v8_index_get__(self, index):
        frames = self._child_window._frame_windows()
        if 0 <= index < len(frames):
            return self._accessor()._view(frames[index])
        deny_cross_origin()

    def __v8_index_set__(self, index, value): deny_cross_origin()
    def __v8_index_del__(self, index): deny_cross_origin()

    def get_window(self): return self
    def get_self(self): return self
    def get_frames(self): return self
    def get_top(self): return self._accessor()._view(self._child_window._top_window)
    def get_parent(self): return self._accessor()._view(self._child_window._parent_window)
    def get_opener(self): return v8n.Null
    def get_length(self): return self._child_window.get_length()
    def get_closed(self): return self._child_window.get_closed()
    def get_location(self): return self._child_window.location if self._same_origin() else self._location
    def set_location(self, value): return self._child_window.location.set_href(value)
    def fn_close(self, *_extra): return self._child_window.fn_close()
    def fn_focus(self, *_extra): return self._child_window.fn_focus()
    def fn_blur(self, *_extra): return self._child_window.fn_blur()
    def fn_postMessage(self, message=MISSING, target_origin="/", transfer=MISSING, *_extra):
        from ...foundation.arguments import sequence

        if message is MISSING:
            raise missing_error("Window", "postMessage", 1, 0)
        if transfer is not MISSING:
            target_origin = dom_string(target_origin)
            transfer = sequence(transfer)
        else:
            transfer = ()
        return self._child_window._post_message(self._accessor(), message, target_origin, transfer)
