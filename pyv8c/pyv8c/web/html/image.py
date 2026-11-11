"""HTML image element."""
from pyv8c import pyv8
from ...idl import interface
from ...foundation.arguments import dom_string, integer
from .elements import HTMLElement
from .utils import url_value, set_url_value


@interface('HTMLImageElement', 'HTMLElement')
class HTMLImageElement(HTMLElement):
    def get_src(self): return url_value(self, "src")
    def set_src(self, value):
        set_url_value(self, "src", value)
        self._reset_image_load()
        self._restart_resource()
    def _reset_image_load(self):
        # A new URL is a new load: nothing has settled and no size is known until it does.
        self._image_complete = False
        self._image_bytes = None
        self._image_type = ""
        self.__dict__.pop("_natural_cache", None)
    def _attribute_changed_here(self, name, old, new):
        super()._attribute_changed_here(name, old, new)
        if name == "src":
            self._reset_image_load()
            self._restart_resource()
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value):
        value = dom_string(value)
        self.attributes['alt'] = str(value)
    def _dimension(self, name):
        """Rendered content size, or the dimension attribute/intrinsic fallback."""
        if self.isConnected:
            from ..dom.layout import layout_snapshot

            boxes, styles = layout_snapshot(self.ownerDocument)
            box = boxes.get(self)
            if box is not None and styles.get(self, {}).get("display") != "none":
                sides = ("left", "right") if name == "width" else ("top", "bottom")
                content = getattr(box, name) - sum(
                    box.padding.get(side, 0) + box.border.get(side, 0) for side in sides)
                return max(0, int(content + 0.5))
        digits = ""
        for character in str(self.attributes.get(name, "")).strip():
            if character not in "0123456789":
                break
            digits += character
        return int(digits) if digits else self._natural_size()[name == "height"]
    def _set_dimension(self, name, value):
        # Not an unsigned long: a negative dimension stores as 0 rather than wrapping.
        self.attributes[name] = str(max(0, integer(value, signed=True)))
    def get_width(self): return self._dimension("width")
    def set_width(self, value): self._set_dimension("width", value)
    def get_height(self): return self._dimension("height")
    def set_height(self, value): self._set_dimension("height", value)
    def get_complete(self):
        """Whether the element is waiting on anything: no source, or a load that has
        settled either way -- Chrome answers false only while one is in flight.
        """
        if not self.attributes.get("src"):
            return True
        if not getattr(self, "_resource_started", False):
            return True
        return bool(getattr(self, "_image_complete", False))

    def fn_decode(self, *_extra):
        """``Promise<void>`` resolved once the image can be used.

        The IDL declared ``HTMLImageElement.decode`` but the runtime shipped no
        ``fn_decode``, so it answered ``pyv8.Undefined`` and ``img.decode().then``
        threw a TypeError (phase10_promise_probe.json).  pyv8c never rasterises
        a bitmap -- ``_image_bytes`` is only read for intrinsic dimensions -- so
        there is nothing to decode: the honest answer is a settled Promise.  A
        resolution is delivered on the realm's microtask queue, never inline.
        """
        return pyv8.JSPromise(resolve=None)

    def _natural_size(self):
        """The size the loaded file declares, read once and kept.

        Chromium answers ``0`` until the image has settled, and the file's own header is what
        it answers with after: no decoded bitmap is needed for the question a page asks.
        """
        cached = getattr(self, "_natural_cache", None)
        if cached is not None:
            return cached
        data = getattr(self, "_image_bytes", None)
        if not isinstance(data, (bytes, bytearray)):
            return (0, 0)
        from ..dom.images import image_size

        size = image_size(data, getattr(self, "_image_type", ""))
        if size != (0, 0):
            self._natural_cache = size
        return size

    def get_naturalWidth(self): return self._natural_size()[0]
    def get_naturalHeight(self): return self._natural_size()[1]

    # -- Reflected attributes the IDL declares but the class never answered.
    #    Chromium reads an absent one back as "" / "auto" / false / 0.  These
    #    are ordinary content attributes, not environment-dependent state.
    def get_currentSrc(self):
        """The selected source, empty until a resource has actually been chosen."""
        return ""
    def get_decoding(self):
        value = str(self.attributes.get("decoding", "")).lower()
        return value if value in ("sync", "async", "auto") else "auto"
    def set_decoding(self, value):
        text = str(dom_string(value)).lower()
        if text in ("sync", "async", "auto"):
            self.attributes["decoding"] = text
    def get_isMap(self): return self._bool("ismap")
    def set_isMap(self, value): self._set_bool("ismap", value)
    def get_loading(self):
        value = str(self.attributes.get("loading", "")).lower()
        return value if value in ("lazy", "eager") else "auto"
    def set_loading(self, value):
        text = str(dom_string(value)).lower()
        if text in ("lazy", "eager"):
            self.attributes["loading"] = text
        else:
            self.attributes.pop("loading", None)
    def get_referrerPolicy(self): return self._attr("referrerpolicy")
    def set_referrerPolicy(self, value): self._set_string("referrerpolicy", value)
    def get_sizes(self): return self._attr("sizes")
    def set_sizes(self, value): self._set_string("sizes", value)
    def get_srcset(self): return self._attr("srcset")
    def set_srcset(self, value): self._set_string("srcset", value)
    def get_useMap(self): return self._attr("usemap")
    def set_useMap(self, value): self._set_string("usemap", value)
    def get_x(self):
        """CSS layout offset; an unrendered element has no box, so Chromium says 0."""
        return 0
    def get_y(self): return 0


