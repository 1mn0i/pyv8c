"""HTML element implementations.

The node tree and document algorithms live in :mod:`dom.model`; this module
owns the HTML tag-specific interfaces and the tag-to-class factory.
"""

from html import escape

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagExposed

from ..bindings.registry import interface, js_attributes, js_methods
from ..core.errors import InvalidCharacterError
from .collections import DOMTokenList, HTMLCollection, NamedNodeMap, _dom_string
from .model import DocumentFragment, Node, _VALID_NAME_RE


@interface("Element", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Element(Node):
    """Base implementation for HTML and XML elements."""

    __v8_attribute__ = js_attributes(
        ("namespaceURI", "get_namespaceURI", None, 0, 1), ("localName", "get_localName", None, 0, 1),
        ("tagName", "get_tagName", None, 0, 1), ("id", "get_id", "set_id", 0, 0),
        ("className", "get_className", "set_className", 0, 0), ("innerHTML", "get_innerHTML", "set_innerHTML", 0, 0),
        ("outerHTML", "get_outerHTML", "set_outerHTML", 0, 0), ("children", "get_children", None, 0, 1),
        ("classList", "get_classList", None, 0, 1), ("attributes", "get_attributes", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("getAttribute", "fn_getAttribute", 1, 1), ("setAttribute", "fn_setAttribute", 2, 0),
        ("removeAttribute", "fn_removeAttribute", 1, 0), ("hasAttribute", "fn_hasAttribute", 1, 1),
        ("querySelector", "fn_querySelector", 1, 1), ("querySelectorAll", "fn_querySelectorAll", 1, 1),
        ("matches", "fn_matches", 1, 1), ("closest", "fn_closest", 1, 1),
    )

    def __init__(self, tag_name="div", page=None, attributes=None, **kw):
        super().__init__(page=page, node_type=Node.ELEMENT_NODE, node_name=str(tag_name).upper(), **kw)
        self.localName, self.attributes = str(tag_name).lower(), dict(attributes or {})

    def get_namespaceURI(self): return "http://www.w3.org/1999/xhtml"
    def get_localName(self): return self.localName
    def get_tagName(self): return self.nodeName
    def get_id(self): return self.attributes.get("id", "")
    def set_id(self, value): self.attributes["id"] = str(value)
    def get_className(self): return self.attributes.get("class", "")
    def set_className(self, value): self.attributes["class"] = str(value)
    def get_innerHTML(self): return "".join(c.outer_html() for c in self._children)
    def set_innerHTML(self, value):
        self._replace_children(())
        if self._page: self._page.document._parse_fragment(str(value), self)
    def get_outerHTML(self): return self.outer_html()
    def set_outerHTML(self, value):
        if self.parentNode: self.parentNode.fn_replaceChild(self._page.document._parse_one(str(value)), self)
    def get_children(self): return HTMLCollection(owner=self, getter=lambda: [c for c in self._children if isinstance(c, Element)])
    def get_classList(self):
        value = getattr(self, "_class_list", None)
        if value is None: value = self._class_list = DOMTokenList(self)
        return value
    def get_attributes(self):
        value = getattr(self, "_attributes_view", None)
        if value is None: value = self._attributes_view = NamedNodeMap(self)
        return value
    def fn_getAttribute(self, name): return self.attributes.get(str(name), pyv8.Null)
    def fn_setAttribute(self, name, value):
        name = str(name)
        if not _VALID_NAME_RE.match(name):
            raise InvalidCharacterError("The attribute name provided is invalid.")
        self.attributes[name.lower()] = str(value)
    def fn_removeAttribute(self, name): self.attributes.pop(str(name).lower(), None)
    def fn_hasAttribute(self, name): return str(name).lower() in self.attributes
    def fn_querySelector(self, selector): return self._page.document.query_selector(selector, self) if self._page else pyv8.Null
    def fn_querySelectorAll(self, selector): return self._page.document.query_selector_all(selector, self) if self._page else []
    def fn_matches(self, selector): return self._page.document.matches(self, selector) if self._page else False
    def fn_closest(self, selector):
        n = self
        while isinstance(n, Element):
            if n.fn_matches(selector): return n
            n = n.parentElement
        return pyv8.Null
    def outer_html(self):
        attrs = "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in self.attributes.items())
        return f"<{self.localName}{attrs}>{self.get_innerHTML()}</{self.localName}>"
    def _clone_shallow(self): return type(self)(self.localName, page=self._page, attributes=self.attributes.copy())


@interface("HTMLElement", "Element")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLElement(Element):
    __v8_attribute__ = js_attributes(("title", "get_title", "set_title", 0, 0), ("hidden", "get_hidden", "set_hidden", 0, 0))
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v): self.attributes["title"] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _resource_attr(name, getter, setter=None):
    return (name, getter, setter, 0, 0)


@interface("HTMLImageElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLImageElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("src", "get_src", "set_src"), _resource_attr("alt", "get_alt", "set_alt"))
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        if self._page: self._page._discover_element_resource(self)
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value): self.attributes["alt"] = str(value)


@interface("HTMLScriptElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLScriptElement(HTMLElement):
    __v8_attribute__ = js_attributes(
        _resource_attr("src", "get_src", "set_src"), _resource_attr("type", "get_type", "set_type"),
        _resource_attr("async", "get_async", "set_async"), _resource_attr("defer", "get_defer", "set_defer"),
    )
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value): self.attributes["src"] = str(value)
    def get_type(self): return self.attributes.get("type", "")
    def set_type(self, value): self.attributes["type"] = str(value)
    def get_async(self): return "async" in self.attributes
    def set_async(self, value):
        if value: self.attributes["async"] = ""
        else: self.attributes.pop("async", None)
    def get_defer(self): return "defer" in self.attributes
    def set_defer(self, value):
        if value: self.attributes["defer"] = ""
        else: self.attributes.pop("defer", None)


@interface("HTMLLinkElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLLinkElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("href", "get_href", "set_href"), _resource_attr("rel", "get_rel", "set_rel"))
    def get_href(self): return self.attributes.get("href", "")
    def set_href(self, value): self.attributes["href"] = str(value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(value)


@interface("HTMLStyleElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLStyleElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("media", "get_media", "set_media"))
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value): self.attributes["media"] = str(value)


@interface("HTMLMetaElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLMetaElement(HTMLElement):
    """The HTML ``meta`` element's reflected string attributes."""

    __v8_attribute__ = js_attributes(
        _resource_attr("name", "get_name", "set_name"),
        _resource_attr("httpEquiv", "get_httpEquiv", "set_httpEquiv"),
        _resource_attr("content", "get_content", "set_content"),
        _resource_attr("media", "get_media", "set_media"),
        _resource_attr("scheme", "get_scheme", "set_scheme"),
    )

    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value): self.attributes["name"] = _dom_string(value)
    def get_httpEquiv(self): return self.attributes.get("http-equiv", "")
    def set_httpEquiv(self, value): self.attributes["http-equiv"] = _dom_string(value)
    def get_content(self): return self.attributes.get("content", "")
    def set_content(self, value): self.attributes["content"] = _dom_string(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value): self.attributes["media"] = _dom_string(value)
    def get_scheme(self): return self.attributes.get("scheme", "")
    def set_scheme(self, value): self.attributes["scheme"] = _dom_string(value)


@interface("HTMLTemplateElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLTemplateElement(HTMLElement):
    __v8_attribute__ = js_attributes(("content", "get_content", None, 0, 1))
    def __init__(self, tag_name="template", page=None, **kw):
        super().__init__(tag_name, page=page, **kw)
        self.content = DocumentFragment(page=page)
    def get_content(self): return self.content


@interface("HTMLIFrameElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLIFrameElement(HTMLElement):
    __v8_attribute__ = js_attributes(
        ("src", "get_src", "set_src", 0, 0), ("contentDocument", "get_contentDocument", None, 0, 1),
        ("contentWindow", "get_contentWindow", None, 0, 1),
    )
    def __init__(self, tag_name="iframe", page=None, **kw):
        super().__init__(tag_name, page=page, **kw); self._child_page = None
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        if self._page and hasattr(self._page, "load_iframe"): self._page.load_iframe(self)
    def get_contentDocument(self): return self._child_page.document if self._child_page else pyv8.Null
    def get_contentWindow(self): return self._child_page.window if self._child_page else pyv8.Null


HTML_CLASSES = {
    **{name: _html_class(name) for name in (
        "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement",
        "HTMLAnchorElement", "HTMLInputElement", "HTMLFormElement", "HTMLCanvasElement", "HTMLMediaElement",
        "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement", "HTMLSelectElement",
        "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement", "HTMLUnknownElement")},
    "HTMLImageElement": HTMLImageElement, "HTMLScriptElement": HTMLScriptElement,
    "HTMLLinkElement": HTMLLinkElement, "HTMLStyleElement": HTMLStyleElement,
    "HTMLMetaElement": HTMLMetaElement, "HTMLTemplateElement": HTMLTemplateElement,
    "HTMLIFrameElement": HTMLIFrameElement,
    # Chromium also resolves the historical lowercase spelling.
    "HTMLIframeElement": HTMLIFrameElement,
}


__all__ = [
    "Element", "HTMLElement", "HTML_CLASSES", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
    "HTMLIFrameElement",
]
