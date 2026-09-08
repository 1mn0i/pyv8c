"""HTML element implementations.

The node tree and document algorithms live in :mod:`dom.model`; this module
owns the HTML tag-specific interfaces and the tag-to-class factory.
"""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

from html import escape

import pyv8



from ...engine.registry import interface
from ...foundation.errors import InvalidCharacterError, NotFoundError, V8SyntaxError, V8TypeError
from .collections import DOMTokenList, HTMLCollection, NamedNodeMap, _dom_string
from .model import (
    Attr, DocumentFragment, Node, Text, XHTML_NAMESPACE, _VALID_NAME_RE,
    _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, _ParentNodeMixin,
)


#: ``insertAdjacent*`` positions, in the order Chromium validates them.
_ADJACENT_POSITIONS = ("beforebegin", "afterbegin", "beforeend", "afterend")


@interface('Element', 'Node')
class Element(_ParentNodeMixin, _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, Node):
    """Base implementation for HTML and XML elements."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(
                name="namespaceURI", getter="get_namespaceURI", side_effect=1
            ),
            AttributeSpec(name="prefix", getter="get_prefix", side_effect=1),
            AttributeSpec(name="localName", getter="get_localName", side_effect=1),
            AttributeSpec(name="tagName", getter="get_tagName", side_effect=1),
            AttributeSpec(name="id", getter="get_id", setter="set_id"),
            AttributeSpec(
                name="className", getter="get_className", setter="set_className"
            ),
            AttributeSpec(
                name="innerHTML", getter="get_innerHTML", setter="set_innerHTML"
            ),
            AttributeSpec(
                name="outerHTML", getter="get_outerHTML", setter="set_outerHTML"
            ),
            AttributeSpec(name="children", getter="get_children", side_effect=1),
            AttributeSpec(name="classList", getter="get_classList", side_effect=1),
            AttributeSpec(name="attributes", getter="get_attributes", side_effect=1),
        ),
        methods=(
            MethodSpec(
                name="getAttribute", callback="fn_getAttribute", length=1, side_effect=1
            ),
            MethodSpec(name="setAttribute", callback="fn_setAttribute", length=2),
            MethodSpec(name="removeAttribute", callback="fn_removeAttribute", length=1),
            MethodSpec(
                name="hasAttribute", callback="fn_hasAttribute", length=1, side_effect=1
            ),
            MethodSpec(name="getAttributeNames", callback="fn_getAttributeNames"),
            MethodSpec(
                name="hasAttributes", callback="fn_hasAttributes", side_effect=1
            ),
            MethodSpec(name="toggleAttribute", callback="fn_toggleAttribute", length=1),
            MethodSpec(
                name="getAttributeNS",
                callback="fn_getAttributeNS",
                length=2,
                side_effect=1,
            ),
            MethodSpec(
                name="removeAttributeNS", callback="fn_removeAttributeNS", length=2
            ),
            MethodSpec(
                name="hasAttributeNS",
                callback="fn_hasAttributeNS",
                length=2,
                side_effect=1,
            ),
            MethodSpec(
                name="getAttributeNode",
                callback="fn_getAttributeNode",
                length=1,
                side_effect=1,
            ),
            MethodSpec(
                name="setAttributeNode", callback="fn_setAttributeNode", length=1
            ),
            MethodSpec(
                name="removeAttributeNode", callback="fn_removeAttributeNode", length=1
            ),
            MethodSpec(
                name="getElementsByTagName",
                callback="fn_getElementsByTagName",
                length=1,
                side_effect=1,
            ),
            MethodSpec(
                name="getElementsByTagNameNS",
                callback="fn_getElementsByTagNameNS",
                length=2,
                side_effect=1,
            ),
            MethodSpec(
                name="getElementsByClassName",
                callback="fn_getElementsByClassName",
                length=1,
                side_effect=1,
            ),
            MethodSpec(
                name="insertAdjacentElement",
                callback="fn_insertAdjacentElement",
                length=2,
            ),
            MethodSpec(
                name="insertAdjacentHTML", callback="fn_insertAdjacentHTML", length=2
            ),
            MethodSpec(
                name="insertAdjacentText", callback="fn_insertAdjacentText", length=2
            ),
            MethodSpec(
                name="querySelector",
                callback="fn_querySelector",
                length=1,
                side_effect=1,
            ),
            MethodSpec(
                name="querySelectorAll",
                callback="fn_querySelectorAll",
                length=1,
                side_effect=1,
            ),
            MethodSpec(name="matches", callback="fn_matches", length=1, side_effect=1),
            MethodSpec(
                name="webkitMatchesSelector",
                callback="fn_webkitMatchesSelector",
                length=1,
                side_effect=1,
            ),
            MethodSpec(name="closest", callback="fn_closest", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, tag_name="div", services=None, attributes=None, **kw):
        super().__init__(services=services, node_type=Node.ELEMENT_NODE, node_name=str(tag_name).upper(), **kw)
        self.localName, self.attributes = str(tag_name).lower(), dict(attributes or {})

    def get_namespaceURI(self): return XHTML_NAMESPACE
    # HTML elements are never qualified, so the prefix is always null.
    def get_prefix(self): return pyv8.Null
    def get_localName(self): return self.localName
    def get_tagName(self): return self.nodeName
    def get_id(self): return self.attributes.get("id", "")
    def set_id(self, value): self.attributes["id"] = str(value)
    def get_className(self): return self.attributes.get("class", "")
    def set_className(self, value): self.attributes["class"] = str(value)
    def get_innerHTML(self): return "".join(c.outer_html() for c in self._children)
    def set_innerHTML(self, value):
        self._replace_children(())
        if self._services: self._services.document_services.document._parse_fragment(str(value), self)
    def get_outerHTML(self): return self.outer_html()
    def set_outerHTML(self, value):
        if self.parentNode: self.parentNode.fn_replaceChild(self._services.document_services.document._parse_one(str(value)), self)
    def get_children(self): return HTMLCollection(owner=self, getter=lambda: [c for c in self._children if isinstance(c, Element)])
    def get_classList(self):
        value = getattr(self, "_class_list", None)
        if value is None: value = self._class_list = DOMTokenList(self)
        return value
    def get_attributes(self):
        value = getattr(self, "_attributes_view", None)
        if value is None: value = self._attributes_view = NamedNodeMap(self)
        return value
    def fn_getAttribute(self, name):
        # HTML attribute names are ASCII case-insensitive, so lookups mirror
        # the lower-casing already applied by ``setAttribute``/``hasAttribute``.
        return self.attributes.get(str(name).lower(), pyv8.Null)
    def fn_setAttribute(self, name, value):
        # Chromium's ``setAttribute`` is ``setAttributeNS`` with a null
        # namespace; routing through it keeps one validation path.
        self.fn_setAttributeNS(None, name, value)
    def fn_removeAttribute(self, name): self.attributes.pop(str(name).lower(), None)
    def fn_hasAttribute(self, name): return str(name).lower() in self.attributes
    def fn_hasAttributes(self): return bool(self.attributes)
    def fn_getAttributeNames(self): return list(self.attributes)
    def fn_toggleAttribute(self, name, force=None):
        name = str(name).lower()
        if force in (None, pyv8.Undefined, pyv8.Null):
            force = not (name in self.attributes)
        force = bool(force)
        if force: self.attributes[name] = ""
        else: self.attributes.pop(name, None)
        return force
    # Only the HTML namespace is modelled, so the *NS variants behave exactly
    # like their namespace-free counterparts for a null/HTML namespace and
    # match nothing for a foreign one.
    def fn_getAttributeNS(self, namespace, local_name): return self.fn_getAttribute(local_name)
    # Not declared on the V8 template: the bundled baseline has an unresolved
    # signature for it and strict validation rejects such declarations.
    def fn_setAttributeNS(self, namespace, name, value):
        name = str(name)
        if not _VALID_NAME_RE.match(name):
            raise InvalidCharacterError("The attribute name provided is invalid.")
        self.attributes[name.lower()] = str(value)
    def fn_removeAttributeNS(self, namespace, local_name): self.fn_removeAttribute(local_name)
    def fn_hasAttributeNS(self, namespace, local_name): return self.fn_hasAttribute(local_name)
    def fn_getAttributeNode(self, name):
        name = str(name).lower()
        if name not in self.attributes: return pyv8.Null
        return Attr(name, self.attributes[name], ownerElement=self, services=self._services)
    def fn_setAttributeNode(self, attr):
        if not isinstance(attr, Attr): raise V8TypeError("parameter 1 is not of type 'Attr'.")
        previous = self.fn_getAttributeNode(attr.get_name())
        self.attributes[str(attr.get_name()).lower()] = attr.get_value()
        return previous
    def fn_removeAttributeNode(self, attr):
        if not isinstance(attr, Attr): raise V8TypeError("parameter 1 is not of type 'Attr'.")
        name = str(attr.get_name()).lower()
        if name not in self.attributes: raise NotFoundError("The attribute was not found.")
        return Attr(name, self.attributes.pop(name), ownerElement=self, services=self._services)
    def _descendant_elements(self):
        return [node for node in self._descendants() if isinstance(node, Element)]
    def fn_getElementsByTagName(self, tag_name):
        wanted = str(tag_name).lower()
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted == "*" or node.localName == wanted
        ])
    def fn_getElementsByClassName(self, class_names):
        wanted = set(str(class_names).split())
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted.issubset(set(node.get_className().split()))
        ])
    def fn_getElementsByTagNameNS(self, namespace, local_name):
        if namespace not in (None, pyv8.Null, pyv8.Undefined, "", "*", XHTML_NAMESPACE):
            return HTMLCollection(owner=self, getter=lambda: [])
        return self.fn_getElementsByTagName(local_name)
    def _insert_adjacent(self, where, nodes):
        where = str(where).lower()
        if where not in _ADJACENT_POSITIONS:
            raise V8SyntaxError(
                f"The value provided ('{where}') is not one of "
                + ", ".join(f"'{p}'" for p in _ADJACENT_POSITIONS) + "."
            )
        if where in ("afterbegin", "beforeend"):
            for offset, node in enumerate(nodes):
                self._adopt_child(node, offset if where == "afterbegin" else None)
            return
        parent = self.parentNode
        if parent is None:
            return
        for node in nodes:
            # Re-locate ``self`` on every step: adopting a node that is
            # already a sibling detaches it first and shifts the list.
            index = parent._children.index(self) + (0 if where == "beforebegin" else 1)
            parent._adopt_child(node, index)
    def fn_insertAdjacentElement(self, where, element):
        if not isinstance(element, Element):
            raise V8TypeError("parameter 2 is not of type 'Element'")
        self._insert_adjacent(where, [element])
        return element
    def fn_insertAdjacentText(self, where, data):
        self._insert_adjacent(where, [Text(str(data), services=self._services)])
    def fn_insertAdjacentHTML(self, position, html):
        document = self._services.document_services.document
        fragment = DocumentFragment(services=self._services)
        document._parse_fragment(str(html), fragment)
        self._insert_adjacent(position, list(fragment._children))
    def fn_webkitMatchesSelector(self, selector): return self.fn_matches(selector)
    def fn_querySelector(self, selector): return self._services.document_services.document.query_selector(selector, self) if self._services else pyv8.Null
    def fn_querySelectorAll(self, selector): return self._services.document_services.document.query_selector_all(selector, self) if self._services else []
    def fn_matches(self, selector): return self._services.document_services.document.matches(self, selector) if self._services else False
    def fn_closest(self, selector):
        n = self
        while isinstance(n, Element):
            if n.fn_matches(selector): return n
            n = n.parentElement
        return pyv8.Null
    def outer_html(self):
        attrs = "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in self.attributes.items())
        return f"<{self.localName}{attrs}>{self.get_innerHTML()}</{self.localName}>"
    def _clone_shallow(self): return type(self)(self.localName, services=self._services, attributes=self.attributes.copy())


@interface('HTMLElement', 'Element')
class HTMLElement(Element):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="title", getter="get_title", setter="set_title"),
            AttributeSpec(name="hidden", getter="get_hidden", setter="set_hidden"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v): self.attributes["title"] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _resource_attr(name, getter, setter=None):
    return AttributeSpec(name=name, getter=getter, setter=setter)


@interface('HTMLImageElement', 'HTMLElement')
class HTMLImageElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _resource_attr("src", "get_src", "set_src"),
            _resource_attr("alt", "get_alt", "set_alt"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        if self._services: self._services.resource_services.discover_element_resource(self)
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value): self.attributes["alt"] = str(value)


@interface('HTMLScriptElement', 'HTMLElement')
class HTMLScriptElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _resource_attr("src", "get_src", "set_src"),
            _resource_attr("type", "get_type", "set_type"),
            _resource_attr("async", "get_async", "set_async"),
            _resource_attr("defer", "get_defer", "set_defer"),
        ),
        constructor_behavior=0,
        has_constructor=1,
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


@interface('HTMLLinkElement', 'HTMLElement')
class HTMLLinkElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _resource_attr("href", "get_href", "set_href"),
            _resource_attr("rel", "get_rel", "set_rel"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_href(self): return self.attributes.get("href", "")
    def set_href(self, value): self.attributes["href"] = str(value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(value)


@interface('HTMLStyleElement', 'HTMLElement')
class HTMLStyleElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(_resource_attr("media", "get_media", "set_media"),),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value): self.attributes["media"] = str(value)


@interface('HTMLMetaElement', 'HTMLElement')
class HTMLMetaElement(HTMLElement):
    """The HTML ``meta`` element's reflected string attributes."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _resource_attr("name", "get_name", "set_name"),
            _resource_attr("httpEquiv", "get_httpEquiv", "set_httpEquiv"),
            _resource_attr("content", "get_content", "set_content"),
            _resource_attr("media", "get_media", "set_media"),
            _resource_attr("scheme", "get_scheme", "set_scheme"),
        ),
        constructor_behavior=0,
        has_constructor=1,
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


@interface('HTMLTemplateElement', 'HTMLElement')
class HTMLTemplateElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="content", getter="get_content", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, tag_name="template", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self.content = DocumentFragment(services=services)
    def get_content(self): return self.content


@interface('HTMLIFrameElement', 'HTMLElement')
class HTMLIFrameElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="src", getter="get_src", setter="set_src"),
            AttributeSpec(
                name="contentDocument", getter="get_contentDocument", side_effect=1
            ),
            AttributeSpec(
                name="contentWindow", getter="get_contentWindow", side_effect=1
            ),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, tag_name="iframe", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self._child_services = None
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        self._resource_started = False
        if self._services:
            resource_services = self._services.resource_services
            if resource_services is not None: resource_services.load_iframe(self)
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
