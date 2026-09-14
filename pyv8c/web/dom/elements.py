"""HTML element implementations.

The node tree and document algorithms live in :mod:`dom.model`; this module
owns the HTML tag-specific interfaces and the tag-to-class factory.
"""
from html import escape
import base64
import io
import math
import re

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string, interface_value, is_nullish, missing_error
from ...foundation.errors import InvalidCharacterError, NotFoundError, V8SyntaxError, V8TypeError
from .collections import DOMTokenList, HTMLCollection, NamedNodeMap, _dom_string
from .model import (
    _VALID_NAME_RE,
    XHTML_NAMESPACE,
    Attr,
    DocumentFragment,
    Node,
    Text,
    _ChildNodeMixin,
    _NonDocumentTypeChildNodeMixin,
    _ParentNodeMixin,
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
            AttributeSpec(name="clientWidth", getter="get_clientWidth", side_effect=1),
            AttributeSpec(name="clientHeight", getter="get_clientHeight", side_effect=1),
            AttributeSpec(name="scrollWidth", getter="get_scrollWidth", side_effect=1),
            AttributeSpec(name="scrollHeight", getter="get_scrollHeight", side_effect=1),
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
            force = not (name in self.attributes)
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
    def get_style(self):
        value = getattr(self, "_style_declaration", None)
        if value is None:
            from .css import CSSStyleDeclaration
            value = self._style_declaration = CSSStyleDeclaration(element=self)
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


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _canvas_color(value):
    value = str(value or "").strip().lower()
    named = {"red": (255, 0, 0, 255), "green": (0, 128, 0, 255), "blue": (0, 0, 255, 255),
             "black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "transparent": (0, 0, 0, 0)}
    if value in named: return named[value]
    if value.startswith("#"):
        h = value[1:]
        if len(h) in (3, 4): h = ''.join(c * 2 for c in h)
        if len(h) == 6: h += "ff"
        try: return tuple(int(h[i:i+2], 16) for i in (0, 2, 4, 6))
        except ValueError: return (0, 0, 0, 255)
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if m:
        p = [x.strip() for x in m.group(1).split(',')]
        try:
            rgb = [round(float(x[:-1]) * 2.55) if x.endswith('%') else int(float(x)) for x in p[:3]]
            a = float(p[3]) * 255 if len(p) > 3 and not p[3].endswith('%') else (float(p[3][:-1]) * 2.55 if len(p) > 3 else 255)
            return tuple(max(0, min(255, int(x))) for x in (*rgb, a))
        except (ValueError, IndexError): pass
    return (0, 0, 0, 255)


@interface('CanvasRenderingContext2D', None)
class CanvasRenderingContext2D:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="canvas", getter="get_canvas", side_effect=1),
                    AttributeSpec(name="fillStyle", getter="get_fillStyle", setter="set_fillStyle"),
                    AttributeSpec(name="strokeStyle", getter="get_strokeStyle", setter="set_strokeStyle"),
                    AttributeSpec(name="font", getter="get_font", setter="set_font")),
        methods=(MethodSpec(name="fillRect", callback="fn_fillRect"), MethodSpec(name="clearRect", callback="fn_clearRect"),
                 MethodSpec(name="fillText", callback="fn_fillText"), MethodSpec(name="strokeText", callback="fn_strokeText"),
                 MethodSpec(name="beginPath", callback="fn_beginPath"), MethodSpec(name="moveTo", callback="fn_moveTo"),
                 MethodSpec(name="lineTo", callback="fn_lineTo"), MethodSpec(name="arc", callback="fn_arc"),
                 MethodSpec(name="stroke", callback="fn_stroke"), MethodSpec(name="strokeRect", callback="fn_strokeRect"),
                 MethodSpec(name="measureText", callback="fn_measureText"), MethodSpec(name="save", callback="fn_save"),
                 MethodSpec(name="restore", callback="fn_restore")), constructor_behavior=0, has_constructor=0)
    def __init__(self, canvas):
        self._canvas = canvas; self._fill = "#000000"; self._stroke = "#000000"; self._font = "10px sans-serif"
        self._path = []; self._stack = []
        try:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (canvas._width, canvas._height), (0, 0, 0, 0)); self._draw = ImageDraw.Draw(self._image)
        except ImportError: self._image = self._draw = None
    def _reset(self):
        if self._image is not None:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (self._canvas._width, self._canvas._height), (0, 0, 0, 0)); self._draw = ImageDraw.Draw(self._image)
    def get_canvas(self): return self._canvas
    def get_fillStyle(self): return self._fill
    def set_fillStyle(self, v): self._fill = str(v)
    def get_strokeStyle(self): return self._stroke
    def set_strokeStyle(self, v): self._stroke = str(v)
    def get_font(self): return self._font
    def set_font(self, v): self._font = str(v)
    def _font_obj(self):
        if self._image is None: return None
        from PIL import ImageFont
        m = re.search(r"([0-9.]+)\s*(px|pt)", self._font); size = float(m.group(1)) if m else 10
        if m and m.group(2) == 'pt': size *= 96 / 72
        family = self._font[m.end():].strip().split(',')[0].strip('"\' ') if m else ''
        candidates = [family, "arial", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
        for f in candidates:
            try:
                if f: return ImageFont.truetype(f, max(1, int(size)))
            except OSError: pass
        return ImageFont.load_default()
    def fn_fillRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw: self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=_canvas_color(self._fill))
    def fn_clearRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw: self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=(0,0,0,0))
    def fn_fillText(self, text='', x=0, y=0, *_):
        if self._draw: self._draw.text((float(x), float(y)), str(text), font=self._font_obj(), fill=_canvas_color(self._fill), anchor='ls')
    def fn_strokeText(self, text='', x=0, y=0, *_):
        if self._draw: self._draw.text((float(x), float(y)), str(text), font=self._font_obj(), fill=None, stroke_width=1, stroke_fill=_canvas_color(self._stroke), anchor='ls')
    def fn_beginPath(self, *_): self._path = []
    def fn_moveTo(self, x=0, y=0, *_): self._path.append([(float(x), float(y))])
    def fn_lineTo(self, x=0, y=0, *_):
        if not self._path: self.fn_moveTo(x, y)
        else: self._path[-1].append((float(x), float(y)))
    def fn_arc(self, x=0, y=0, r=0, start=0, end=0, anticlockwise=False, *_):
        n = max(8, int(abs(float(end)-float(start))*24)); pts = [(float(x)+float(r)*math.cos(float(start)+(float(end)-float(start))*i/n), float(y)+float(r)*math.sin(float(start)+(float(end)-float(start))*i/n)) for i in range(n+1)]
        self._path.append(pts)
    def fn_stroke(self, *_):
        if self._draw:
            for p in self._path:
                if len(p) > 1: self._draw.line(p, fill=_canvas_color(self._stroke), width=1)
    def fn_strokeRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw: self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), outline=_canvas_color(self._stroke), width=1)
    def fn_measureText(self, text='', *_):
        class Metrics: pass
        m = Metrics(); m.width = self._draw.textlength(str(text), font=self._font_obj()) if self._draw else len(str(text))*10; return m
    def fn_save(self, *_): self._stack.append((self._fill, self._stroke, self._font))
    def fn_restore(self, *_):
        if self._stack: self._fill, self._stroke, self._font = self._stack.pop()


@interface('HTMLCanvasElement', 'HTMLElement')
class HTMLCanvasElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(attributes=(AttributeSpec(name="width", getter="get_width", setter="set_width"), AttributeSpec(name="height", getter="get_height", setter="set_height")), methods=(MethodSpec(name="getContext", callback="fn_getContext"), MethodSpec(name="toDataURL", callback="fn_toDataURL")), constructor_behavior=0, has_constructor=1)
    def __init__(self, tag_name="canvas", services=None, **kw):
        super().__init__(tag_name, services=services, **kw); self._width=300; self._height=150; self._context2d=None
    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, v): self._width=max(0, int(float(v))); self.attributes['width']=str(self._width); self._resize()
    def get_height(self): return self._height
    def set_height(self, v): self._height=max(0, int(float(v))); self.attributes['height']=str(self._height); self._resize()
    def fn_getContext(self, kind='', *_):
        if str(kind).lower() != '2d': return pyv8.Null
        if self._context2d is None: self._context2d=CanvasRenderingContext2D(self)
        return self._context2d
    def fn_toDataURL(self, kind='image/png', *_):
        if self._context2d is None: self.fn_getContext('2d')
        out=io.BytesIO(); self._context2d._image.save(out, format='PNG'); return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii')


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
        value = dom_string(value)
        self.attributes["src"] = str(value)
        if self._services: self._services.resource_services.discover_element_resource(self)
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value):
        value = dom_string(value)
        self.attributes['alt'] = str(value)


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
    def set_src(self, value):
        value = dom_string(value)
        self.attributes['src'] = str(value)
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
            _resource_attr("href", "get_href", "set_href"),
            _resource_attr("rel", "get_rel", "set_rel"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def get_href(self): return self.attributes.get("href", "")
    def set_href(self, value):
        value = dom_string(value)
        self.attributes['href'] = str(value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value):
        value = dom_string(value)
        self.attributes['rel'] = str(value)


@interface('HTMLStyleElement', 'HTMLElement')
class HTMLStyleElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(_resource_attr("media", "get_media", "set_media"),),
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
    def set_name(self, value):
        value = dom_string(value)
        self.attributes['name'] = _dom_string(value)
    def get_httpEquiv(self): return self.attributes.get("http-equiv", "")
    def set_httpEquiv(self, value):
        value = dom_string(value)
        self.attributes['http-equiv'] = _dom_string(value)
    def get_content(self): return self.attributes.get("content", "")
    def set_content(self, value):
        value = dom_string(value)
        self.attributes['content'] = _dom_string(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = _dom_string(value)
    def get_scheme(self): return self.attributes.get("scheme", "")
    def set_scheme(self, value):
        value = dom_string(value)
        self.attributes['scheme'] = _dom_string(value)


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
        value = dom_string(value)
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
        "HTMLAnchorElement", "HTMLInputElement", "HTMLFormElement", "HTMLMediaElement",
        "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement", "HTMLSelectElement",
        "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement", "HTMLUnknownElement")},
    "HTMLImageElement": HTMLImageElement, "HTMLScriptElement": HTMLScriptElement, "HTMLCanvasElement": HTMLCanvasElement,
    "HTMLLinkElement": HTMLLinkElement, "HTMLStyleElement": HTMLStyleElement,
    "HTMLMetaElement": HTMLMetaElement, "HTMLTemplateElement": HTMLTemplateElement,
    "HTMLIFrameElement": HTMLIFrameElement,
    # Chromium also resolves the historical lowercase spelling.
    "HTMLIframeElement": HTMLIFrameElement,
}


__all__ = [
    "Element", "HTMLElement", "HTML_CLASSES", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
    "HTMLIFrameElement", "HTMLCanvasElement",
]
