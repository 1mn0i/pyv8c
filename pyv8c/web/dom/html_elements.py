"""HTML element implementations.

The node tree lives in :mod:`dom.nodes`, the document algorithms in
:mod:`dom.document` and the shared tag helpers in :mod:`dom.utils`; this
module owns the HTML tag-specific interfaces and the tag-to-class factory.
"""
from html import escape
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string, interface_value, is_nullish, missing_error
from ...foundation.errors import InvalidCharacterError, NotFoundError, V8SyntaxError, V8TypeError
from ...foundation.urls import origin_string
from .character_data import Text
from .collections import (
    DOMStringMap,
    DOMTokenList,
    HTMLCollection,
    HTMLFormControlsCollection,
    NamedNodeMap,
)
from .css import CSSStyleDeclaration
from .mixins import _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, _ParentNodeMixin
from .nodes import Attr, DocumentFragment, Node
from .utils import _VALID_NAME_RE, XHTML_NAMESPACE

#: ``insertAdjacent*`` positions, in the order Chromium validates them.
_ADJACENT_POSITIONS = ("beforebegin", "afterbegin", "beforeend", "afterend")


@interface('Element', 'Node')
class Element(_ParentNodeMixin, _ChildNodeMixin, _NonDocumentTypeChildNodeMixin, Node):
    """Base implementation for HTML and XML elements."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="namespaceURI", getter="get_namespaceURI", side_effect=1),
            AttributeSpec(name="prefix", getter="get_prefix", side_effect=1),
            AttributeSpec(name="localName", getter="get_localName", side_effect=1),
            AttributeSpec(name="tagName", getter="get_tagName", side_effect=1),
            AttributeSpec(name="id", getter="get_id", setter="set_id"),
            AttributeSpec(name="className", getter="get_className", setter="set_className"),
            AttributeSpec(name="innerHTML", getter="get_innerHTML", setter="set_innerHTML"),
            AttributeSpec(name="outerHTML", getter="get_outerHTML", setter="set_outerHTML"),
            AttributeSpec(name="children", getter="get_children", side_effect=1),
            AttributeSpec(name="classList", getter="get_classList", side_effect=1),
            AttributeSpec(name="attributes", getter="get_attributes", side_effect=1),
            AttributeSpec(name="clientWidth", getter="get_clientWidth", side_effect=1),
            AttributeSpec(name="clientHeight", getter="get_clientHeight", side_effect=1),
            AttributeSpec(name="scrollWidth", getter="get_scrollWidth", side_effect=1),
            AttributeSpec(name="scrollHeight", getter="get_scrollHeight", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getAttribute", callback="fn_getAttribute", length=1, side_effect=1),
            MethodSpec(name="setAttribute", callback="fn_setAttribute", length=2),
            MethodSpec(name="removeAttribute", callback="fn_removeAttribute", length=1),
            MethodSpec(name="hasAttribute", callback="fn_hasAttribute", length=1, side_effect=1),
            MethodSpec(name="getAttributeNames", callback="fn_getAttributeNames"),
            MethodSpec(name="hasAttributes", callback="fn_hasAttributes", side_effect=1),
            MethodSpec(name="toggleAttribute", callback="fn_toggleAttribute", length=1),
            MethodSpec(name="getAttributeNS", callback="fn_getAttributeNS", length=2, side_effect=1),
            MethodSpec(name="removeAttributeNS", callback="fn_removeAttributeNS", length=2),
            MethodSpec(name="hasAttributeNS", callback="fn_hasAttributeNS", length=2, side_effect=1),
            MethodSpec(name="getAttributeNode", callback="fn_getAttributeNode", length=1, side_effect=1),
            MethodSpec(name="setAttributeNode", callback="fn_setAttributeNode", length=1),
            MethodSpec(name="removeAttributeNode", callback="fn_removeAttributeNode", length=1),
            MethodSpec(name="getElementsByTagName", callback="fn_getElementsByTagName", length=1, side_effect=1),
            MethodSpec(name="getElementsByTagNameNS", callback="fn_getElementsByTagNameNS", length=2, side_effect=1),
            MethodSpec(name="getElementsByClassName", callback="fn_getElementsByClassName", length=1, side_effect=1),
            MethodSpec(name="insertAdjacentElement", callback="fn_insertAdjacentElement", length=2),
            MethodSpec(name="insertAdjacentHTML", callback="fn_insertAdjacentHTML", length=2),
            MethodSpec(name="insertAdjacentText", callback="fn_insertAdjacentText", length=2),
            MethodSpec(name="querySelector", callback="fn_querySelector", length=1, side_effect=1),
            MethodSpec(name="querySelectorAll", callback="fn_querySelectorAll", length=1, side_effect=1),
            MethodSpec(name="matches", callback="fn_matches", length=1, side_effect=1),
            MethodSpec(name="webkitMatchesSelector", callback="fn_webkitMatchesSelector", length=1, side_effect=1),
            MethodSpec(name="closest", callback="fn_closest", length=1, side_effect=1),
            MethodSpec(name="getBoundingClientRect", callback="fn_getBoundingClientRect", side_effect=1),
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
    def set_id(self, value):
        value = dom_string(value)
        self.attributes['id'] = str(value)
    def get_className(self): return self.attributes.get("class", "")
    def set_className(self, value):
        value = dom_string(value)
        self.attributes['class'] = str(value)
    def get_innerHTML(self): return "".join(c.outer_html() for c in self._children)
    def set_innerHTML(self, value):
        value = dom_string(value)
        self._replace_children(())
        if self._services: self._services.document_services.document._parse_fragment(str(value), self)
    def get_outerHTML(self): return self.outer_html()
    def set_outerHTML(self, value):
        value = dom_string(value)
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
    def get_clientWidth(self):
        from .layout import layout
        return int(round(layout(self).width))
    def get_clientHeight(self):
        from .layout import layout
        return int(round(layout(self).height))
    def get_scrollWidth(self): return self.get_clientWidth()
    def get_scrollHeight(self): return self.get_clientHeight()
    def fn_getBoundingClientRect(self, *_extra):
        from .layout import layout
        box = layout(self)
        return {"x": box.left, "y": box.top, "top": box.top, "left": box.left,
                "right": box.left + box.width, "bottom": box.top + box.height,
                "width": box.width, "height": box.height}
    def fn_getAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'getAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        return self.attributes.get(str(name).lower(), pyv8.Null)
    def fn_setAttribute(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'setAttribute', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('Element', 'setAttribute', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self.fn_setAttributeNS(None, name, value)
    def fn_removeAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'removeAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        self.attributes.pop(str(name).lower(), None)
    def fn_hasAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'hasAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        return str(name).lower() in self.attributes
    def fn_hasAttributes(self, *_extra):
        return bool(self.attributes)
    def fn_getAttributeNames(self, *_extra):
        return list(self.attributes)
    def fn_toggleAttribute(self, name=MISSING, force=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'toggleAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        if force is not pyv8.Undefined:
            force = boolean(force)
        name = str(name).lower()
        if force in (None, pyv8.Undefined, pyv8.Null):
            force = name not in self.attributes
        force = bool(force)
        if force: self.attributes[name] = ""
        else: self.attributes.pop(name, None)
        return force
    # Only the HTML namespace is modelled, so the *NS variants behave exactly
    # like their namespace-free counterparts for a null/HTML namespace and
    # match nothing for a foreign one.
    def fn_getAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'getAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'getAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        return self.fn_getAttribute(local_name)
    def fn_setAttributeNS(self, namespace=MISSING, name=MISSING, value=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 0)
        if name is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 1)
        if value is MISSING:
            raise missing_error('Element', 'setAttributeNS', 3, 2)
        namespace = None if is_nullish(namespace) else dom_string(namespace)
        name = dom_string(name)
        value = dom_string(value)
        if not _VALID_NAME_RE.match(name):
            raise InvalidCharacterError("The attribute name provided is invalid.")
        self.attributes[name.lower()] = str(value)
    def fn_removeAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'removeAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'removeAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        self.fn_removeAttribute(local_name)
    def fn_hasAttributeNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'hasAttributeNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'hasAttributeNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
        return self.fn_hasAttribute(local_name)
    def fn_getAttributeNode(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Element', 'getAttributeNode', 1, 0, constructor=False)
        name = dom_string(name)
        name = str(name).lower()
        if name not in self.attributes: return pyv8.Null
        return Attr(name, self.attributes[name], ownerElement=self, services=self._services)
    def fn_setAttributeNode(self, attr=MISSING, *_extra):
        if attr is MISSING:
            raise missing_error('Element', 'setAttributeNode', 1, 0, constructor=False)
        attr = interface_value(attr, 'Attr', 'Element', 'setAttributeNode', 1, nullable=False)
        if not isinstance(attr, Attr): raise V8TypeError("parameter 1 is not of type 'Attr'.")
        previous = self.fn_getAttributeNode(attr.get_name())
        self.attributes[str(attr.get_name()).lower()] = attr.get_value()
        return previous
    def fn_removeAttributeNode(self, attr=MISSING, *_extra):
        if attr is MISSING:
            raise missing_error('Element', 'removeAttributeNode', 1, 0, constructor=False)
        attr = interface_value(attr, 'Attr', 'Element', 'removeAttributeNode', 1, nullable=False)
        if not isinstance(attr, Attr): raise V8TypeError("parameter 1 is not of type 'Attr'.")
        name = str(attr.get_name()).lower()
        if name not in self.attributes: raise NotFoundError("The attribute was not found.")
        return Attr(name, self.attributes.pop(name), ownerElement=self, services=self._services)
    def _descendant_elements(self):
        return [node for node in self._descendants() if isinstance(node, Element)]
    def fn_getElementsByTagName(self, tag_name=MISSING, *_extra):
        if tag_name is MISSING:
            raise missing_error('Element', 'getElementsByTagName', 1, 0, constructor=False)
        tag_name = dom_string(tag_name)
        wanted = str(tag_name).lower()
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted == "*" or node.localName == wanted
        ])
    def fn_getElementsByClassName(self, class_names=MISSING, *_extra):
        if class_names is MISSING:
            raise missing_error('Element', 'getElementsByClassName', 1, 0, constructor=False)
        class_names = dom_string(class_names)
        wanted = set(str(class_names).split())
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendant_elements()
            if wanted.issubset(set(node.get_className().split()))
        ])
    def fn_getElementsByTagNameNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Element', 'getElementsByTagNameNS', 2, 0, constructor=False)
        if local_name is MISSING:
            raise missing_error('Element', 'getElementsByTagNameNS', 2, 1, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        local_name = dom_string(local_name)
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
    def fn_insertAdjacentElement(self, where=MISSING, element=MISSING, *_extra):
        if where is MISSING:
            raise missing_error('Element', 'insertAdjacentElement', 2, 0, constructor=False)
        if element is MISSING:
            raise missing_error('Element', 'insertAdjacentElement', 2, 1, constructor=False)
        where = dom_string(where)
        element = interface_value(element, 'Element', 'Element', 'insertAdjacentElement', 2, nullable=False)
        if not isinstance(element, Element):
            raise V8TypeError("parameter 2 is not of type 'Element'")
        self._insert_adjacent(where, [element])
        return element
    def fn_insertAdjacentText(self, where=MISSING, data=MISSING, *_extra):
        if where is MISSING:
            raise missing_error('Element', 'insertAdjacentText', 2, 0, constructor=False)
        if data is MISSING:
            raise missing_error('Element', 'insertAdjacentText', 2, 1, constructor=False)
        where = dom_string(where)
        data = dom_string(data)
        self._insert_adjacent(where, [Text(str(data), services=self._services)])
    def fn_insertAdjacentHTML(self, position=MISSING, html=MISSING, *_extra):
        if position is MISSING:
            raise missing_error('Element', 'insertAdjacentHTML', 2, 0, constructor=False)
        if html is MISSING:
            raise missing_error('Element', 'insertAdjacentHTML', 2, 1, constructor=False)
        position = dom_string(position)
        html = dom_string(html)
        document = self._services.document_services.document
        fragment = DocumentFragment(services=self._services)
        document._parse_fragment(str(html), fragment)
        self._insert_adjacent(position, list(fragment._children))
    def fn_webkitMatchesSelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'webkitMatchesSelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self.fn_matches(selector)
    def fn_querySelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'querySelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.query_selector(selector, self) if self._services else pyv8.Null
    def fn_querySelectorAll(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'querySelectorAll', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.query_selector_all(selector, self) if self._services else []
    def fn_matches(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'matches', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self._services.document_services.document.matches(self, selector) if self._services else False
    def fn_closest(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Element', 'closest', 1, 0, constructor=False)
        selector = dom_string(selector)
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
            AttributeSpec(name="style", getter="get_style", side_effect=1),
            AttributeSpec(name="dataset", getter="get_dataset", side_effect=1),
            AttributeSpec(name="offsetParent", getter="get_offsetParent", side_effect=1),
            AttributeSpec(name="offsetTop", getter="get_offsetTop", side_effect=1),
            AttributeSpec(name="offsetLeft", getter="get_offsetLeft", side_effect=1),
            AttributeSpec(name="offsetWidth", getter="get_offsetWidth", side_effect=1),
            AttributeSpec(name="offsetHeight", getter="get_offsetHeight", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v):
        v = dom_string(v)
        self.attributes['title'] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        v = boolean(v)
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)
    def _attr(self, name):
        """The value of a content attribute as a string, ``""`` when absent."""
        value = self.attributes.get(name)
        return "" if value is None else str(value)
    def _set_string(self, name, value):
        self.attributes[name] = str(dom_string(value))
    def _bool(self, name): return name in self.attributes
    def _set_bool(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)
    def get_style(self):
        value = getattr(self, "_style_declaration", None)
        if value is None:
            value = self._style_declaration = CSSStyleDeclaration(element=self)
        return value
    def get_dataset(self):
        value = getattr(self, "_dataset_map", None)
        if value is None:
            value = self._dataset_map = DOMStringMap(self)
        return value
    def get_offsetParent(self):
        if not self.isConnected: return pyv8.Null
        return self.parentElement or pyv8.Null
    def get_offsetTop(self):
        from .layout import layout
        return int(round(layout(self).top))
    def get_offsetLeft(self): return 0
    def get_offsetWidth(self):
        from .layout import layout
        return int(round(layout(self).width))
    def get_offsetHeight(self):
        from .layout import layout
        return int(round(layout(self).height))


@interface('HTMLUnknownElement', 'HTMLElement')
class HTMLUnknownElement(HTMLElement):
    """The interface Chromium exposes for tags it has no dedicated class for.

    That covers the obsolete tags (``bgsound``, ``blink``, ``isindex``,
    ``keygen``, ``multicol``, ``nextid``, ``spacer``), custom element names and
    anything else this DOM does not model.  The baseline declares no members
    beyond ``HTMLElement`` and ``allow_new=false``, so the constructor throws;
    the host still builds instances through ``__init__``.
    """

    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0,
        has_constructor=1,
        to_string_tag="HTMLUnknownElement",
    )


#: The tags a form collects: its listed elements.  An ``input`` in the
#: image-button state is submittable but is not one of them.
_LISTED_ELEMENT_TAGS = frozenset((
    "button", "fieldset", "input", "object", "output", "select", "textarea",
))


def _is_listed_element(node):
    """Whether ``node`` is a listed element, by tag and input type."""
    if not isinstance(node, Element) or node.localName not in _LISTED_ELEMENT_TAGS:
        return False
    if node.localName != "input":
        return True
    return str(node.attributes.get("type", "")).strip().lower() != "image"


@interface('HTMLFormElement', 'HTMLElement')
class HTMLFormElement(HTMLElement):
    """``<form>``: the controls it owns.

    ``elements`` holds the listed elements whose form owner this form is --
    descendants, and the controls elsewhere in the document that name it with
    ``form=`` -- which is why the list follows the document's order and not the
    form's.  A control's owner is the form its ``form`` attribute names, and the
    nearest ancestor form only when it has no such attribute, so a control naming a
    form that is not there belongs to no form at all.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="elements", getter="get_elements", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def _owns(self, node):
        """Whether this form is ``node``'s form owner."""
        if "form" in node.attributes:
            form_id = str(node.attributes.get("form", "")).strip()
            lookup = getattr(self.root_node(), "fn_getElementById", None)
            owner = lookup(form_id) if lookup is not None and form_id else None
            return owner is self
        for ancestor in node._ancestors():
            if isinstance(ancestor, HTMLFormElement):
                return ancestor is self
        return False
    def _controls(self):
        return [node for node in self.root_node()._descendants()
                if _is_listed_element(node) and self._owns(node)]
    def get_elements(self):
        value = getattr(self, "_elements_collection", None)
        if value is None:
            value = self._elements_collection = HTMLFormControlsCollection(
                owner=self, getter=self._controls)
        return value
    def get_length(self):
        return len(self._controls())


#: The states ``button.type`` reports.  A missing attribute and a value the button
#: does not know are both the submit button.
_BUTTON_TYPES = frozenset(("submit", "reset", "button"))


@interface('HTMLButtonElement', 'HTMLElement')
class HTMLButtonElement(HTMLElement):
    """``<button>``: its name and value, and the state ``type`` reports."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", setter="set_name"),
            AttributeSpec(name="value", getter="get_value", setter="set_value"),
            AttributeSpec(name="type", getter="get_type", setter="set_type"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_name(self): return self._attr("name")
    def set_name(self, value): self._set_string("name", value)
    def get_value(self): return self._attr("value")
    def set_value(self, value): self._set_string("value", value)
    def get_type(self):
        value = self._attr("type").lower()
        return value if value in _BUTTON_TYPES else "submit"
    def set_type(self, value): self._set_string("type", value)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _attribute(name, getter, setter=None):
    return AttributeSpec(name=name, getter=getter, setter=setter)


def _document_url(element):
    """The URL a relative attribute on *element* resolves against."""
    return str(getattr(getattr(element, "ownerDocument", None), "url", "") or "")


def _url_value(element, name):
    """A URL attribute's value, resolved against the element's document.

    Chromium's URL getters return an absolute URL while the content attribute
    keeps exactly what the page wrote, and an absent or empty attribute reads
    back as an empty string rather than as the document's own URL.
    """
    raw = str(element.attributes.get(name, ""))
    return urljoin(_document_url(element), raw) if raw else ""


def _set_url_value(element, name, value):
    element.attributes[name] = str(dom_string(value))


def _url_attr(name, getter, setter):
    """Declare a URL-reflecting attribute; pair it with the two helpers above."""
    return _attribute(name, getter, setter)


@interface('HTMLImageElement', 'HTMLElement')
class HTMLImageElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("src", "get_src", "set_src"),
            _attribute("alt", "get_alt", "set_alt"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value):
        _set_url_value(self, "src", value)
        if self._services: self._services.resource_services.discover_element_resource(self)
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value):
        value = dom_string(value)
        self.attributes['alt'] = str(value)


@interface('HTMLScriptElement', 'HTMLElement')
class HTMLScriptElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("src", "get_src", "set_src"),
            _attribute("type", "get_type", "set_type"),
            _attribute("async", "get_async", "set_async"),
            _attribute("defer", "get_defer", "set_defer"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_type(self): return self.attributes.get("type", "")
    def set_type(self, value):
        value = dom_string(value)
        self.attributes['type'] = str(value)
    def get_async(self): return "async" in self.attributes
    def set_async(self, value):
        value = boolean(value)
        if value: self.attributes["async"] = ""
        else: self.attributes.pop("async", None)
    def get_defer(self): return "defer" in self.attributes
    def set_defer(self, value):
        value = boolean(value)
        if value: self.attributes["defer"] = ""
        else: self.attributes.pop("defer", None)


@interface('HTMLLinkElement', 'HTMLElement')
class HTMLLinkElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("href", "get_href", "set_href"),
            _attribute("rel", "get_rel", "set_rel"),
            _attribute("media", "get_media", "set_media"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_href(self): return _url_value(self, "href")
    def set_href(self, value): _set_url_value(self, "href", value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value):
        value = dom_string(value)
        self.attributes['rel'] = str(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)


@interface('HTMLAnchorElement', 'HTMLElement')
class HTMLAnchorElement(HTMLElement):
    """``<a>``, adding the URL decomposition members ``href`` carries.

    The getters share :func:`_url_value` with the other URL attributes, and
    assigning one of them parses the current href, replaces that component and
    writes the result back into the attribute, as URLUtils requires.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("href", "get_href", "set_href"),
            _attribute("origin", "get_origin"),
            _attribute("protocol", "get_protocol", "set_protocol"),
            _attribute("username", "get_username", "set_username"),
            _attribute("password", "get_password", "set_password"),
            _attribute("host", "get_host", "set_host"),
            _attribute("hostname", "get_hostname", "set_hostname"),
            _attribute("port", "get_port", "set_port"),
            _attribute("pathname", "get_pathname", "set_pathname"),
            _attribute("search", "get_search", "set_search"),
            _attribute("hash", "get_hash", "set_hash"),
        ),
        methods=(MethodSpec(name="toString", callback="fn_toString"),),
        constructor_behavior=0,
        has_constructor=1,
    )

    def _href(self):
        """The href every URL member below is derived from."""
        return _url_value(self, "href")

    def _parts(self):
        return urlsplit(self._href())

    def _set_component(self, **changes):
        self.attributes['href'] = urlunsplit(self._parts()._replace(**changes))

    def get_href(self): return self._href()
    def set_href(self, value): _set_url_value(self, "href", value)
    def get_origin(self): return origin_string(self._href()) if self._href() else ""
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ":"
    def set_protocol(self, value): self._set_component(scheme=str(dom_string(value)).rstrip(":"))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        self._set_credentials(username=str(dom_string(value)), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        self._set_credentials(username=self.get_username(), password=str(dom_string(value)))
    def _set_credentials(self, *, username, password):
        parts = self._parts()
        host = parts.hostname or ""
        if parts.port is not None:
            host += ":" + str(parts.port)
        credentials = (username + (":" + password if password else "") + "@") if username or password else ""
        self._set_component(netloc=credentials + host)
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value): self._set_component(netloc=str(dom_string(value)))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._set_component(netloc=credentials + str(dom_string(value)) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        value = str(dom_string(value))
        self._set_component(netloc=credentials + (parts.hostname or "") + ((":" + value) if value else ""))
    def get_pathname(self):
        parts = self._parts()
        return parts.path or ("/" if parts.netloc else "")
    def set_pathname(self, value): self._set_component(path=str(dom_string(value)) or "/")
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value): self._set_component(query=str(dom_string(value)).lstrip("?"))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value): self._set_component(fragment=str(dom_string(value)).lstrip("#"))
    def fn_toString(self, *_extra): return self._href()


@interface('HTMLStyleElement', 'HTMLElement')
class HTMLStyleElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(_attribute("media", "get_media", "set_media"),),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)


@interface('HTMLMetaElement', 'HTMLElement')
class HTMLMetaElement(HTMLElement):
    """The HTML ``meta`` element's reflected string attributes."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _attribute("name", "get_name", "set_name"),
            _attribute("httpEquiv", "get_httpEquiv", "set_httpEquiv"),
            _attribute("content", "get_content", "set_content"),
            _attribute("media", "get_media", "set_media"),
            _attribute("scheme", "get_scheme", "set_scheme"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value):
        value = dom_string(value)
        self.attributes['name'] = dom_string(value)
    def get_httpEquiv(self): return self.attributes.get("http-equiv", "")
    def set_httpEquiv(self, value):
        value = dom_string(value)
        self.attributes['http-equiv'] = dom_string(value)
    def get_content(self): return self.attributes.get("content", "")
    def set_content(self, value):
        value = dom_string(value)
        self.attributes['content'] = dom_string(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = dom_string(value)
    def get_scheme(self): return self.attributes.get("scheme", "")
    def set_scheme(self, value):
        value = dom_string(value)
        self.attributes['scheme'] = dom_string(value)


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
            _url_attr("src", "get_src", "set_src"),
            AttributeSpec(name="contentDocument", getter="get_contentDocument", side_effect=1),
            AttributeSpec(name="contentWindow", getter="get_contentWindow", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, tag_name="iframe", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self._child_services = None
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value):
        _set_url_value(self, "src", value)
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


__all__ = [
    "Element", "HTMLElement", "HTMLUnknownElement", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
    "HTMLIFrameElement",
]
