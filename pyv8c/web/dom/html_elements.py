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
from ...foundation.arguments import MISSING, boolean, dom_string, integer, interface_value, is_nullish, missing_error, number
from ...foundation.errors import InvalidCharacterError, NotFoundError, V8SyntaxError, V8TypeError, dom_exception
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
from .events import EventTarget
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
        lowered = str(name).lower()
        previous = self.attributes.pop(lowered, None)
        if previous is not None:
            from .mutation import record_mutation
            record_mutation(self, "attributes", attribute_name=lowered, old_value=previous)
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
        lowered = name.lower()
        previous = self.attributes.get(lowered)
        self.attributes[lowered] = str(value)
        from .mutation import record_mutation
        record_mutation(self, "attributes", attribute_name=lowered,
                        old_value=previous)
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
            AttributeSpec(name="action", getter="get_action", setter="set_action"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_action(self):
        """The submission URL: the document's own URL, without its fragment, when
        the attribute is absent."""
        return _url_value(self, "action") or _document_url(self).split("#", 1)[0]
    def set_action(self, value): _set_url_value(self, "action", value)
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


def _owning_table(row):
    """The nearest ``table`` ancestor of *row*, which is the table that counts it."""
    parent = getattr(row, "parentElement", None)
    while parent is not None:
        if isinstance(parent, HTMLTableElement):
            return parent
        parent = getattr(parent, "parentElement", None)
    return None


@interface('HTMLTableElement', 'HTMLElement')
class HTMLTableElement(HTMLElement):
    """``<table>``: the rows a page walks.

    ``rows`` holds every row this table owns -- those in its sections, in document
    order -- and not the rows of a table nested inside it, which belong to that
    table instead.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="rows", getter="get_rows", side_effect=1),),
        constructor_behavior=0,
        has_constructor=1,
    )

    def get_rows(self):
        view = getattr(self, "_rows_view", None)
        if view is None:
            view = self._rows_view = HTMLCollection(owner=self, getter=self._rows)
        return view

    def _rows(self):
        return [node for node in self._descendants()
                if isinstance(node, HTMLTableRowElement) and _owning_table(node) is self]


@interface('HTMLTableRowElement', 'HTMLElement')
class HTMLTableRowElement(HTMLElement):
    """``<tr>``: the cells it holds and where the row sits in its table."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="cells", getter="get_cells", side_effect=1),
            AttributeSpec(name="rowIndex", getter="get_rowIndex"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def get_cells(self):
        view = getattr(self, "_cells_view", None)
        if view is None:
            view = self._cells_view = HTMLCollection(owner=self, getter=self._cells)
        return view

    def _cells(self):
        """A row's own cells: its ``td`` and ``th`` children, in document order."""
        return [child for child in self._children if isinstance(child, HTMLTableCellElement)]

    def get_rowIndex(self):
        """The row's position in its table's ``rows``, or -1 when no table owns it."""
        table = _owning_table(self)
        if table is None:
            return -1
        return next((index for index, row in enumerate(table._rows()) if row is self), -1)


@interface('HTMLTableCellElement', 'HTMLElement')
class HTMLTableCellElement(HTMLElement):
    """``<td>`` and ``<th>``: where the cell sits in its row."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="cellIndex", getter="get_cellIndex"),),
        constructor_behavior=0,
        has_constructor=1,
    )

    def get_cellIndex(self):
        """The cell's position in its row's ``cells``, or -1 when no row holds it."""
        row = getattr(self, "parentElement", None)
        if not isinstance(row, HTMLTableRowElement):
            return -1
        return next((index for index, cell in enumerate(row._cells()) if cell is self), -1)


#: What ``canPlayType`` answers, measured from Chrome.
_PROBABLY = frozenset({"audio/mpeg", "audio/mp3"})
_MAYBE = frozenset({
    "audio/mp4", "audio/ogg", "audio/wav", "audio/webm", "audio/aac", "audio/opus",
    "audio/flac", "audio/x-wav", "video/mp4", "video/webm", "video/ogg",
})


@interface('TimeRanges', None)
class TimeRanges:
    """A media element's ranges: nothing has loaded, so there are none."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="start", callback="fn_start", length=1),
            MethodSpec(name="end", callback="fn_end", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="TimeRanges",
        has_constructor=1,
    )

    def get_length(self): return 0
    def fn_start(self, index=MISSING, *_extra): return self._refuse('start', index)
    def fn_end(self, index=MISSING, *_extra): return self._refuse('end', index)

    @staticmethod
    def _refuse(member, index):
        if index is MISSING:
            raise missing_error('TimeRanges', member, 1, 0, constructor=False)
        raise dom_exception("IndexSizeError",
                            f"Failed to execute '{member}' on 'TimeRanges': There is no range"
                            " at that index.")


@interface('TextTrackList', 'EventTarget')
class TextTrackList(EventTarget):
    """A media element's text tracks: with nothing loaded there are none."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="TextTrackList",
        has_constructor=1,
    )

    def get_length(self): return 0


@interface('HTMLMediaElement', 'HTMLElement')
class HTMLMediaElement(HTMLElement):
    """``<audio>`` and ``<video>``: the playback state a page reads.

    Nothing decodes here, so the state is what a fresh element reports, and
    ``canPlayType`` answers the MIME types Chrome answers.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="src", getter="get_src", setter="set_src"),
            AttributeSpec(name="currentSrc", getter="get_currentSrc", side_effect=1),
            AttributeSpec(name="crossOrigin", getter="get_crossOrigin", side_effect=1),
            AttributeSpec(name="paused", getter="get_paused", side_effect=1),
            AttributeSpec(name="ended", getter="get_ended", side_effect=1),
            AttributeSpec(name="currentTime", getter="get_currentTime", setter="set_currentTime"),
            AttributeSpec(name="duration", getter="get_duration", side_effect=1),
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="networkState", getter="get_networkState", side_effect=1),
            AttributeSpec(name="volume", getter="get_volume", setter="set_volume"),
            AttributeSpec(name="muted", getter="get_muted", setter="set_muted"),
            AttributeSpec(name="loop", getter="get_loop", setter="set_loop"),
            AttributeSpec(name="autoplay", getter="get_autoplay", setter="set_autoplay"),
            AttributeSpec(name="controls", getter="get_controls", setter="set_controls"),
            AttributeSpec(name="defaultPlaybackRate", getter="get_defaultPlaybackRate", side_effect=1),
            AttributeSpec(name="playbackRate", getter="get_playbackRate", setter="set_playbackRate"),
            AttributeSpec(name="seeking", getter="get_seeking", side_effect=1),
            AttributeSpec(name="error", getter="get_error", side_effect=1),
            AttributeSpec(name="preload", getter="get_preload", side_effect=1),
            AttributeSpec(name="textTracks", getter="get_textTracks", side_effect=1),
            AttributeSpec(name="buffered", getter="get_buffered", side_effect=1),
            AttributeSpec(name="played", getter="get_played", side_effect=1),
            AttributeSpec(name="seekable", getter="get_seekable", side_effect=1),
        ),
        methods=(
            MethodSpec(name="canPlayType", callback="fn_canPlayType", length=1, side_effect=1),
            MethodSpec(name="play", callback="fn_play", length=0, side_effect=1),
            MethodSpec(name="pause", callback="fn_pause", length=0),
            MethodSpec(name="load", callback="fn_load", length=0),
        ),
        constructor_behavior=0,
        to_string_tag="HTMLMediaElement",
        has_constructor=1,
    )

    def _media(self, name, default):
        values = getattr(self, "_media_values", None)
        if values is None:
            values = self._media_values = {}
        return values.get(name, default)

    def _set_media(self, name, value):
        self._media(name, None)
        self._media_values[name] = value

    def _flag(self, name): return name in self.attributes
    def _set_flag(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)

    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_currentSrc(self): return ""          # nothing was ever loaded
    def get_crossOrigin(self): return self.attributes.get("crossorigin") or pyv8.Null
    def get_paused(self): return True
    def get_ended(self): return False
    def get_currentTime(self): return float(self._media("currentTime", 0.0))
    def set_currentTime(self, value): self._set_media("currentTime", float(number(value)))
    def get_duration(self): return float("nan")
    def get_readyState(self): return 0
    def get_networkState(self): return 0
    def get_volume(self): return float(self._media("volume", 1.0))
    def set_volume(self, value): self._set_media("volume", float(number(value)))
    def get_muted(self): return self._flag("muted")
    def set_muted(self, value): self._set_flag("muted", value)
    def get_loop(self): return self._flag("loop")
    def set_loop(self, value): self._set_flag("loop", value)
    def get_autoplay(self): return self._flag("autoplay")
    def set_autoplay(self, value): self._set_flag("autoplay", value)
    def get_controls(self): return self._flag("controls")
    def set_controls(self, value): self._set_flag("controls", value)
    def get_defaultPlaybackRate(self): return 1.0
    def get_playbackRate(self): return float(self._media("playbackRate", 1.0))
    def set_playbackRate(self, value): self._set_media("playbackRate", float(number(value)))
    def get_seeking(self): return False
    def get_error(self): return pyv8.Null
    def get_preload(self): return str(self.attributes.get("preload") or "metadata")

    def _empty(self, name, factory):
        value = getattr(self, name, None)
        if value is None:
            value = setattr(self, name, factory())
            value = getattr(self, name)
        return value

    def get_textTracks(self): return self._empty("_text_tracks", TextTrackList)
    def get_buffered(self): return self._empty("_buffered", TimeRanges)
    def get_played(self): return self._empty("_played", TimeRanges)
    def get_seekable(self): return self._empty("_seekable", TimeRanges)

    def fn_canPlayType(self, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('HTMLMediaElement', 'canPlayType', 1, 0, constructor=False)
        value = str(dom_string(type)).strip().lower()
        if value in _PROBABLY: return "probably"
        return "maybe" if value in _MAYBE else ""

    def fn_play(self, *_extra):
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(None) if realm is not None else None
    def fn_pause(self, *_extra): return None
    def fn_load(self, *_extra): return None


@interface('HTMLAudioElement', 'HTMLMediaElement')
class HTMLAudioElement(HTMLMediaElement):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, to_string_tag="HTMLAudioElement", has_constructor=1,
    )


@interface('HTMLVideoElement', 'HTMLMediaElement')
class HTMLVideoElement(HTMLMediaElement):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, to_string_tag="HTMLVideoElement", has_constructor=1,
    )


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
            AttributeSpec(name="width", getter="get_width", setter="set_width"),
            AttributeSpec(name="height", getter="get_height", setter="set_height"),
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
    def _dimension(self, name):
        """One image dimension: the number the attribute starts with, else 0.

        Chromium parses these as dimension values, so ``12px`` and ``12%`` read back
        as 12, and an attribute with no leading number at all as 0.
        """
        digits = ""
        for character in str(self.attributes.get(name, "")).strip():
            if character not in "0123456789":
                break
            digits += character
        return int(digits) if digits else 0
    def _set_dimension(self, name, value):
        # A dimension is not an unsigned long: a negative one is stored as 0 rather
        # than wrapped around.
        self.attributes[name] = str(max(0, integer(value, signed=True)))
    def get_width(self): return self._dimension("width")
    def set_width(self, value): self._set_dimension("width", value)
    def get_height(self): return self._dimension("height")
    def set_height(self, value): self._set_dimension("height", value)


@interface('HTMLScriptElement', 'HTMLElement')
class HTMLScriptElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            _url_attr("src", "get_src", "set_src"),
            _attribute("type", "get_type", "set_type"),
            _attribute("async", "get_async", "set_async"),
            _attribute("defer", "get_defer", "set_defer"),
            AttributeSpec(name="innerText", getter="get_innerText"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_innerText(self):
        """A script is not rendered, so its text is what ``innerText`` reads."""
        return self.get_textContent()
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


__all__ = [
    "Element", "HTMLElement", "HTMLUnknownElement", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
]
