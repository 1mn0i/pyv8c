"""``HTMLIFrameElement``: the frame element and the document it holds.

The reflected attributes are Chromium's, including the values a missing or
unrecognised attribute reads back as; the browsing context itself is built by
the resource layer, which this element only asks for.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string
from .collections import DOMTokenList
from .html_elements import HTMLElement, _attribute, _set_url_value, _url_attr, _url_value

_SANDBOX_TOKENS = (
    "allow-downloads", "allow-forms", "allow-modals", "allow-orientation-lock",
    "allow-pointer-lock", "allow-popups", "allow-popups-to-escape-sandbox",
    "allow-presentation", "allow-same-origin", "allow-scripts",
    "allow-storage-access-by-user-activation", "allow-top-navigation",
    "allow-top-navigation-by-user-activation",
    "allow-top-navigation-to-custom-protocols",
)

_REFERRER_POLICIES = (
    "no-referrer", "no-referrer-when-downgrade", "origin",
    "origin-when-cross-origin", "same-origin", "strict-origin",
    "strict-origin-when-cross-origin", "unsafe-url",
)


@interface('HTMLIFrameElement', 'HTMLElement')
class HTMLIFrameElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("src", "get_src", "set_src"),
            _attribute("srcdoc", "get_srcdoc", "set_srcdoc"),
            _attribute("name", "get_name", "set_name"),
            AttributeSpec(name="sandbox", getter="get_sandbox", side_effect=1),
            _attribute("allow", "get_allow", "set_allow"),
            _attribute("allowFullscreen", "get_allowFullscreen", "set_allowFullscreen"),
            _attribute("width", "get_width", "set_width"),
            _attribute("height", "get_height", "set_height"),
            _attribute("referrerPolicy", "get_referrerPolicy", "set_referrerPolicy"),
            _attribute("loading", "get_loading", "set_loading"),
            AttributeSpec(name="contentDocument", getter="get_contentDocument", side_effect=1),
            AttributeSpec(name="contentWindow", getter="get_contentWindow", side_effect=1),
        ),
        methods=(MethodSpec(name="getSVGDocument", callback="fn_getSVGDocument", side_effect=1),),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, tag_name="iframe", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self._child_services = None

    def _is_connected(self):
        """Whether this element is currently attached to a Document.

        Chromium does not start an iframe navigation while the element is
        detached.  The browsing context is created by the adoption hook when
        it is inserted instead.
        """
        node = self
        while node is not None:
            if getattr(node, "nodeType", None) == 9:  # Document
                return True
            node = getattr(node, "parentNode", None)
        return False

    def _on_removed_from_document(self):
        if self._child_services is not None:
            self._services.resource_services.discard_iframe_context(self)

    def _attribute_changed(self, name, old, new):
        """Apply the navigation side effects of reflected content attributes."""
        if old == new:
            return
        if name == "name":
            if self._child_services is not None:
                self._child_services.window._window_name = str(new or "")
            return
        if name not in ("src", "srcdoc"):
            return
        if name == "src" and "srcdoc" in self.attributes:
            return
        self._resource_started = False
        if not self._is_connected() or self._services is None:
            return
        resources = self._services.resource_services
        if resources is None:
            return
        self._services.realm_services.post_task(lambda: resources.load_iframe(self)
                                                if self._is_connected() else None)

    def fn_setAttributeNS(self, namespace=MISSING, name=MISSING, value=MISSING, *_extra):
        old = self.attributes.get(str(name).lower()) if name is not MISSING else None
        result = super().fn_setAttributeNS(namespace, name, value, *_extra)
        if name is not MISSING:
            self._attribute_changed(str(name).lower(), old,
                                    self.attributes.get(str(name).lower()))
        return result

    def fn_removeAttribute(self, name=MISSING, *_extra):
        old_name = None if name is MISSING else str(dom_string(name)).lower()
        old = self.attributes.get(old_name) if old_name is not None else None
        result = super().fn_removeAttribute(name, *_extra)
        if old_name is not None:
            self._attribute_changed(old_name, old, self.attributes.get(old_name))
        return result
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value):
        _set_url_value(self, "src", value)
        self._resource_started = False
        if self._services and self._is_connected() and "srcdoc" not in self.attributes:
            resource_services = self._services.resource_services
            if resource_services is not None: resource_services.load_iframe(self)
    def get_srcdoc(self): return self.attributes.get("srcdoc", "")
    def set_srcdoc(self, value):
        markup = str(dom_string(value))
        old = self.attributes.get("srcdoc")
        self.attributes["srcdoc"] = markup
        self._resource_started = False
        if self._services is not None and self._is_connected() and old != markup:
            resource_services = self._services.resource_services
            if resource_services is not None:
                self._services.realm_services.post_task(
                    lambda: resource_services.load_iframe_markup(self, markup))
    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value):
        self.attributes["name"] = str(dom_string(value))
        if self._child_services is not None:
            self._child_services.window._window_name = str(self.attributes["name"])
    def get_sandbox(self):
        tokens = getattr(self, "_sandbox_tokens", None)
        if tokens is None: tokens = self._sandbox_tokens = DOMTokenList(self, "sandbox", _SANDBOX_TOKENS)
        return tokens
    def get_allow(self): return self.attributes.get("allow", "")
    def set_allow(self, value): self.attributes["allow"] = str(dom_string(value))
    def get_allowFullscreen(self): return "allowfullscreen" in self.attributes
    def set_allowFullscreen(self, value):
        if boolean(value): self.attributes["allowfullscreen"] = ""
        else: self.attributes.pop("allowfullscreen", None)
    def get_width(self): return self.attributes.get("width", "")
    def set_width(self, value): self.attributes["width"] = str(dom_string(value))
    def get_height(self): return self.attributes.get("height", "")
    def set_height(self, value): self.attributes["height"] = str(dom_string(value))
    def get_referrerPolicy(self):
        policy = str(self.attributes.get("referrerpolicy", ""))
        return policy if policy in _REFERRER_POLICIES else ""
    def set_referrerPolicy(self, value):
        policy = str(dom_string(value))
        self.attributes["referrerpolicy"] = policy if policy in _REFERRER_POLICIES else ""
    def get_loading(self):
        loading = str(self.attributes.get("loading", ""))
        return loading if loading in ("lazy", "eager") else "auto"
    def set_loading(self, value):
        loading = str(dom_string(value))
        self.attributes["loading"] = loading if loading in ("lazy", "eager") else "auto"
    def fn_getSVGDocument(self, *_args): return pyv8.Null
    def get_contentDocument(self):
        if self._child_services is None:
            return pyv8.Null
        from ..bom.window import Window
        accessor = Window._accessor_window(self._services.document_services.window)
        if accessor._origin != self._child_services._origin:
            return pyv8.Null
        return self._child_services.document_services.document
    def get_contentWindow(self):
        if self._child_services is None:
            return pyv8.Null
        child_window = self._child_services.document_services.window
        from ..bom.window import Window
        parent_window = Window._accessor_window(self._services.document_services.window)
        return parent_window._view(child_window)


__all__ = ["HTMLIFrameElement"]
