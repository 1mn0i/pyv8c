"""DOM node model: nodes, elements, HTML element classes and documents.

Collection views live in :mod:`dom.collections` and the event model in
:mod:`dom.events`; both are re-exported here so legacy ``model`` imports and
``from .model import *`` keep working.
"""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

import re
from html import escape

import pyv8

from pyv8.flag import V8Attribute

from ...engine.registry import interface
from ...foundation.errors import V8TypeError, HierarchyRequestError, NotFoundError, InvalidCharacterError

from .collections import NodeList, HTMLCollection, HTMLAllCollection, DOMTokenList, NamedNodeMap
from .events import (
    Event,
    UIEvent,
    MouseEvent,
    KeyboardEvent,
    CustomEvent,
    EventTarget,
)

__all__ = [
    "NodeList", "HTMLCollection", "HTMLAllCollection", "DOMTokenList",
    "Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "EventTarget",
    "Node", "DocumentFragment", "Attr", "NamedNodeMap", "CharacterData", "Text",
    "Comment", "DocumentType", "Element", "HTMLElement", "HTML_CLASSES",
    "HTMLImageElement", "HTMLScriptElement", "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement",
    "HTMLTemplateElement", "HTMLIFrameElement", "Document",
]

_VALID_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")

#: The only namespace this DOM implementation models (HTML documents).
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"


@interface('Node', 'EventTarget')
class Node(EventTarget):
    ELEMENT_NODE, ATTRIBUTE_NODE, TEXT_NODE, COMMENT_NODE = 1, 2, 3, 8
    CDATA_SECTION_NODE, ENTITY_REFERENCE_NODE, ENTITY_NODE = 4, 5, 6
    PROCESSING_INSTRUCTION_NODE, NOTATION_NODE = 7, 12
    DOCUMENT_NODE, DOCUMENT_TYPE_NODE, DOCUMENT_FRAGMENT_NODE = 9, 10, 11
    DOCUMENT_POSITION_DISCONNECTED = 1
    DOCUMENT_POSITION_PRECEDING = 2
    DOCUMENT_POSITION_FOLLOWING = 4
    DOCUMENT_POSITION_CONTAINS = 8
    DOCUMENT_POSITION_CONTAINED_BY = 16
    DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC = 32
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(
                name="nodeType",
                getter="get_nodeType",
                property_flags=V8Attribute.DontEnum,
                side_effect=1,
            ),
            AttributeSpec(name="nodeName", getter="get_nodeName", side_effect=1),
            AttributeSpec(name="baseURI", getter="get_baseURI", side_effect=1),
            AttributeSpec(name="isConnected", getter="get_isConnected", side_effect=1),
            AttributeSpec(
                name="ownerDocument", getter="get_ownerDocument", side_effect=1
            ),
            AttributeSpec(name="parentNode", getter="get_parentNode", side_effect=1),
            AttributeSpec(
                name="parentElement", getter="get_parentElement", side_effect=1
            ),
            AttributeSpec(name="childNodes", getter="get_childNodes", side_effect=1),
            AttributeSpec(name="firstChild", getter="get_firstChild", side_effect=1),
            AttributeSpec(name="lastChild", getter="get_lastChild", side_effect=1),
            AttributeSpec(
                name="previousSibling", getter="get_previousSibling", side_effect=1
            ),
            AttributeSpec(name="nextSibling", getter="get_nextSibling", side_effect=1),
            AttributeSpec(
                name="textContent", getter="get_textContent", setter="set_textContent"
            ),
            AttributeSpec(
                name="nodeValue", getter="get_nodeValue", setter="set_nodeValue"
            ),
        ),
        methods=(
            MethodSpec(name="appendChild", callback="fn_appendChild", length=1),
            MethodSpec(name="insertBefore", callback="fn_insertBefore", length=2),
            MethodSpec(name="removeChild", callback="fn_removeChild", length=1),
            MethodSpec(name="replaceChild", callback="fn_replaceChild", length=2),
            MethodSpec(name="cloneNode", callback="fn_cloneNode"),
            MethodSpec(
                name="contains", callback="fn_contains", length=1, side_effect=1
            ),
            MethodSpec(
                name="hasChildNodes", callback="fn_hasChildNodes", side_effect=1
            ),
            MethodSpec(name="isEqualNode", callback="fn_isEqualNode", length=1),
            MethodSpec(
                name="compareDocumentPosition",
                callback="fn_compareDocumentPosition",
                length=1,
            ),
            MethodSpec(name="getRootNode", callback="fn_getRootNode"),
            MethodSpec(
                name="isDefaultNamespace", callback="fn_isDefaultNamespace", length=1
            ),
            MethodSpec(name="isSameNode", callback="fn_isSameNode", length=1),
            MethodSpec(
                name="lookupNamespaceURI", callback="fn_lookupNamespaceURI", length=1
            ),
            MethodSpec(name="lookupPrefix", callback="fn_lookupPrefix", length=1),
            MethodSpec(name="normalize", callback="fn_normalize"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, services=None, node_type=0, node_name="", value=None, **kw):
        super().__init__(services=services, **kw)
        self.nodeType, self.nodeName, self.nodeValue = node_type, node_name, value
        self.parentNode = None
        self._children = []
        self._child_nodes_view = NodeList(owner=self, getter=lambda: self._children)

    @property
    def parentElement(self):
        return self.parentNode if isinstance(self.parentNode, Element) else None
    @property
    def firstChild(self): return self._children[0] if self._children else pyv8.Null
    @property
    def lastChild(self): return self._children[-1] if self._children else pyv8.Null

    @property
    def ownerDocument(self):
        """The document this node belongs to; ``None`` for a Document itself.

        A detached node keeps the document that created it, which is why this
        reads the realm's document rather than walking up to the root.
        """
        if self.nodeType == Node.DOCUMENT_NODE:
            return None
        document_services = getattr(getattr(self, "_services", None), "document_services", None)
        return getattr(document_services, "document", None)

    @property
    def isConnected(self):
        return isinstance(self.root_node(), Document)

    @property
    def baseURI(self):
        document = self.ownerDocument
        return document.url if document is not None else ""

    def root_node(self):
        """The topmost ancestor, i.e. the root of this node's tree."""
        node = self
        while node.parentNode is not None:
            node = node.parentNode
        return node

    def _sibling(self, step):
        """The adjacent sibling in ``step`` direction, or ``None``."""
        parent = self.parentNode
        if parent is None:
            return None
        siblings = parent._children
        try:
            index = siblings.index(self) + step
        except ValueError:
            return None
        return siblings[index] if 0 <= index < len(siblings) else None

    @property
    def previousSibling(self): return self._sibling(-1)
    @property
    def nextSibling(self): return self._sibling(1)

    def _ancestors(self):
        """Yield ``self`` followed by its ancestors, nearest first."""
        node = self
        while node is not None:
            yield node
            node = node.parentNode

    def _descendants(self):
        """Yield every descendant of ``self`` in tree order."""
        for child in self._children:
            yield child
            if isinstance(child, Node):
                yield from child._descendants()

    def get_nodeType(self): return self.nodeType
    def get_nodeName(self): return self.nodeName
    def get_baseURI(self): return self.baseURI
    def get_isConnected(self): return self.isConnected
    def get_ownerDocument(self): return self.ownerDocument or pyv8.Null
    def get_previousSibling(self): return self.previousSibling or pyv8.Null
    def get_nextSibling(self): return self.nextSibling or pyv8.Null
    def get_parentNode(self): return self.parentNode or pyv8.Null
    def get_parentElement(self): return self.parentElement or pyv8.Null
    def get_childNodes(self): return self._child_nodes_view
    def get_firstChild(self): return self.firstChild
    def get_lastChild(self): return self.lastChild
    def get_textContent(self): return "".join(n.get_textContent() or "" for n in self._children)
    def set_textContent(self, value):
        self._replace_children(())
        # Replacing with the empty string leaves no child node behind (the
        # spec removes a text node whose data is empty after replacement).
        if value is not None and str(value) != "":
            self.append_child(Text(str(value), services=self._services))
    def get_nodeValue(self): return self.nodeValue if self.nodeValue is not None else pyv8.Null
    def set_nodeValue(self, value): self.nodeValue = value

    @staticmethod
    def _detach_from_parent(node):
        parent = node.parentNode
        if parent is not None:
            try:
                parent._children.remove(node)
            except ValueError:
                pass
            node.parentNode = None

    def _ensure_adoptable(self, node):
        if not isinstance(node, Node): raise V8TypeError("The new child is not a Node")
        # A HierarchyRequestError only fires when ``node`` is an inclusive
        # ancestor of ``self`` (the parent itself or one of its ancestors).
        # Re-inserting a node this parent already contains is a plain *move*
        # in the DOM, so the containment check must look at the other
        # direction: inserting an existing descendant simply relocates it.
        if node is self or node.fn_contains(self):
            raise HierarchyRequestError("The new child element contains the parent.")

    def _adopt_child(self, node, index=None):
        self._ensure_adoptable(node)
        self._detach_from_parent(node)
        node.parentNode = self
        if index is None:
            self._children.append(node)
        else:
            self._children.insert(index, node)
        services = getattr(self, "_services", None)
        document_services = getattr(services, "document_services", None)
        if document_services is not None and not document_services.parsing:
            callback = getattr(document_services, "node_adopted", None)
            if callback is not None: callback(node)
        return node

    def _replace_children(self, nodes):
        for child in self._children:
            child.parentNode = None
        self._children = []
        for node in nodes:
            self._adopt_child(node)

    def append_child(self, node):
        if isinstance(node, DocumentFragment):
            for child in list(node._children):
                self._adopt_child(child)
            return node
        return self._adopt_child(node)
    def fn_appendChild(self, node): return self.append_child(node)
    def fn_insertBefore(self, node, reference):
        if reference in (None, pyv8.Null, pyv8.Undefined): return self.append_child(node)
        if reference not in self._children: raise NotFoundError("The reference node is not a child.")
        index = self._children.index(reference)
        if isinstance(node, DocumentFragment):
            for child in list(node._children):
                self._adopt_child(child, index)
                index += 1
            return node
        if node.parentNode is self and self._children.index(node) < index:
            index -= 1
        return self._adopt_child(node, index)
    def fn_removeChild(self, node):
        if node not in self._children: raise NotFoundError("The node to be removed is not a child.")
        self._children.remove(node); node.parentNode = None; return node
    def fn_replaceChild(self, new, old):
        if not isinstance(new, Node) or not isinstance(old, Node):
            raise V8TypeError("Failed to execute 'replaceChild' on 'Node': parameter is not of type 'Node'.")
        if old not in self._children: raise NotFoundError("The node to be replaced is not a child.")
        i = self._children.index(old)
        self.fn_removeChild(old)
        if isinstance(new, DocumentFragment):
            for child in list(new._children):
                self._adopt_child(child, i)
                i += 1
            return old
        if new.parentNode is self and self._children.index(new) < i:
            i -= 1
        self._adopt_child(new, i)
        return old
    def fn_cloneNode(self, deep=False):
        clone = self._clone_shallow()
        if deep:
            for child in self._children: clone.append_child(child.fn_cloneNode(True))
        return clone
    def _clone_shallow(self): return Node(services=self._services, node_type=self.nodeType, node_name=self.nodeName, value=self.nodeValue)
    def fn_compareDocumentPosition(self, other):
        """Bitmask describing ``other``'s position relative to ``self``."""
        if not isinstance(other, Node):
            raise V8TypeError("Failed to execute 'compareDocumentPosition' on 'Node': parameter 1 is not of type 'Node'.")
        if other is self:
            return 0
        if self.root_node() is not other.root_node():
            # Trees have no defined relative order; the standard explicitly
            # leaves the preceding/following choice to the implementation.
            return (Node.DOCUMENT_POSITION_DISCONNECTED
                    | Node.DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC
                    | Node.DOCUMENT_POSITION_FOLLOWING)
        self_path = list(self._ancestors())
        other_path = list(other._ancestors())
        if other in self_path:
            return Node.DOCUMENT_POSITION_CONTAINS | Node.DOCUMENT_POSITION_PRECEDING
        if self in other_path:
            return Node.DOCUMENT_POSITION_CONTAINED_BY | Node.DOCUMENT_POSITION_FOLLOWING
        # Neither contains the other: order is decided by the two branches
        # that leave their closest common ancestor.
        common = next(node for node in self_path if node in other_path)
        self_branch = self_path[self_path.index(common) - 1]
        other_branch = other_path[other_path.index(common) - 1]
        siblings = common._children
        if self_branch in siblings and other_branch in siblings:
            return (Node.DOCUMENT_POSITION_FOLLOWING
                    if siblings.index(self_branch) < siblings.index(other_branch)
                    else Node.DOCUMENT_POSITION_PRECEDING)
        return (Node.DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC
                | Node.DOCUMENT_POSITION_FOLLOWING)

    def fn_getRootNode(self, options=None):
        # Shadow trees are not modelled, so ``composed`` cannot change the
        # answer: the root is always the topmost node of this tree.
        return self.root_node()

    def fn_isSameNode(self, other=None): return other is self

    def fn_isDefaultNamespace(self, namespace=None): return namespace == XHTML_NAMESPACE

    def fn_lookupNamespaceURI(self, prefix=None):
        if prefix in (None, pyv8.Null, pyv8.Undefined, ""):
            return XHTML_NAMESPACE
        return pyv8.Null

    def fn_lookupPrefix(self, namespace=None): return pyv8.Null

    def fn_normalize(self):
        """Merge adjacent text nodes and drop empty ones, recursively."""
        previous = None
        for child in list(self._children):
            if isinstance(child, Text):
                if not child.nodeValue:
                    self._children.remove(child); child.parentNode = None; continue
                if isinstance(previous, Text):
                    previous.nodeValue = (previous.nodeValue or "") + (child.nodeValue or "")
                    self._children.remove(child); child.parentNode = None
                    continue
            previous = child
        for child in list(self._children):
            if isinstance(child, Node):
                child.fn_normalize()

    def fn_contains(self, other): return other is self or any(c.fn_contains(other) for c in self._children)
    def fn_hasChildNodes(self): return bool(self._children)
    def fn_isEqualNode(self, other): return isinstance(other, Node) and self.outer_html() == other.outer_html()
    def outer_html(self): return self.get_textContent()


def _as_nodes(values, parent):
    """Convert a Web IDL ``(Node or DOMString)...`` argument list to nodes."""
    nodes = []
    for value in values:
        if isinstance(value, Node):
            nodes.append(value)
        elif value is not None and value is not pyv8.Undefined and value is not pyv8.Null:
            nodes.append(Text(str(value), services=getattr(parent, "_services", None)))
    return nodes


class _NonDocumentTypeChildNodeMixin:
    """``previousElementSibling`` / ``nextElementSibling``.

    Included by :class:`Element` and :class:`CharacterData`; document type
    nodes are excluded, matching the ``NonDocumentTypeChildNode`` mixin.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(
                name="previousElementSibling",
                getter="get_previousElementSibling",
                side_effect=1,
            ),
            AttributeSpec(
                name="nextElementSibling",
                getter="get_nextElementSibling",
                side_effect=1,
            ),
        ),
    )

    def _element_sibling(self, step):
        parent = self.parentNode
        if parent is None:
            return None
        siblings = parent._children
        try:
            index = siblings.index(self) + step
        except ValueError:
            return None
        while 0 <= index < len(siblings):
            if isinstance(siblings[index], Element):
                return siblings[index]
            index += step
        return None

    def get_previousElementSibling(self): return self._element_sibling(-1) or pyv8.Null
    def get_nextElementSibling(self): return self._element_sibling(1) or pyv8.Null


class _ParentNodeMixin:
    """Element children plus ``append`` / ``prepend`` / ``replaceChildren``.

    Included by :class:`Element`, :class:`Document` and
    :class:`DocumentFragment`; :class:`Document` deliberately leaves out the
    ``ChildNode`` operations, which it does not have in Chromium.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(
                name="firstElementChild", getter="get_firstElementChild", side_effect=1
            ),
            AttributeSpec(
                name="lastElementChild", getter="get_lastElementChild", side_effect=1
            ),
            AttributeSpec(
                name="childElementCount", getter="get_childElementCount", side_effect=1
            ),
        ),
        methods=(
            MethodSpec(name="append", callback="fn_append"),
            MethodSpec(name="prepend", callback="fn_prepend"),
            MethodSpec(name="replaceChildren", callback="fn_replaceChildren"),
        ),
    )

    def _element_children(self):
        return [child for child in self._children if isinstance(child, Element)]

    def get_firstElementChild(self):
        children = self._element_children()
        return children[0] if children else pyv8.Null
    def get_lastElementChild(self):
        children = self._element_children()
        return children[-1] if children else pyv8.Null
    def get_childElementCount(self): return len(self._element_children())

    def fn_append(self, *values):
        for node in _as_nodes(values, self):
            self.append_child(node)
    def fn_prepend(self, *values):
        for index, node in enumerate(_as_nodes(values, self)):
            self._adopt_child(node, index)
    def fn_replaceChildren(self, *values):
        self._replace_children(())
        for node in _as_nodes(values, self):
            self.append_child(node)


class _ChildNodeMixin:
    """``before`` / ``after`` / ``replaceWith`` / ``remove``.

    Included by :class:`Element`, :class:`CharacterData` and
    :class:`DocumentType` -- the three interfaces carrying ``ChildNode``.
    """

    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="before", callback="fn_before"),
            MethodSpec(name="after", callback="fn_after"),
            MethodSpec(name="replaceWith", callback="fn_replaceWith"),
            MethodSpec(name="remove", callback="fn_remove"),
        ),
    )

    def fn_before(self, *values):
        parent = self.parentNode
        if parent is None:
            return
        for node in _as_nodes(values, self):
            parent._adopt_child(node, parent._children.index(self))

    def fn_after(self, *values):
        parent = self.parentNode
        if parent is None:
            return
        for node in _as_nodes(values, self):
            parent._adopt_child(node, parent._children.index(self) + 1)

    def fn_replaceWith(self, *values):
        parent = self.parentNode
        nodes = _as_nodes(values, self)
        if parent is None:
            return
        index = parent._children.index(self)
        self.fn_remove()
        for offset, node in enumerate(nodes):
            parent._adopt_child(node, index + offset)

    def fn_remove(self):
        parent = self.parentNode
        if parent is not None:
            parent.fn_removeChild(self)


@interface('DocumentFragment', 'Node')
class DocumentFragment(_ParentNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=1, has_constructor=1)
    def __init__(self, services=None, **kw):
        super().__init__(services=services, node_type=Node.DOCUMENT_FRAGMENT_NODE, node_name="#document-fragment", **kw)
    def _clone_shallow(self): return DocumentFragment(services=self._services)


@interface('Attr', 'Node')
class Attr(Node):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="value", getter="get_value", setter="set_value"),
            AttributeSpec(name="specified", getter="get_specified", side_effect=1),
            AttributeSpec(
                name="namespaceURI", getter="get_namespaceURI", side_effect=1
            ),
            AttributeSpec(name="prefix", getter="get_prefix", side_effect=1),
            AttributeSpec(name="localName", getter="get_localName", side_effect=1),
            AttributeSpec(
                name="ownerElement", getter="get_ownerElement", side_effect=1
            ),
        ),
        constructor_behavior=0,
        to_string_tag="Attr",
        has_constructor=1,
    )
    def __init__(self, name="", value="", ownerElement=None, services=None, **kw):
        super().__init__(services=services, node_type=Node.ATTRIBUTE_NODE, node_name=str(name), value=str(value), **kw)
        self.ownerElement = ownerElement
    def get_name(self): return self.nodeName
    def get_value(self): return self.nodeValue or ""
    def set_value(self, value): self.nodeValue = str(value)
    def get_specified(self): return True
    # HTML attributes carry no namespace, so both namespace accessors are null.
    def get_namespaceURI(self): return pyv8.Null
    def get_prefix(self): return pyv8.Null
    def get_localName(self): return self.nodeName
    def get_ownerElement(self): return self.ownerElement or pyv8.Null


@interface('CharacterData', 'Node')
class CharacterData(_ChildNodeMixin, _NonDocumentTypeChildNodeMixin, Node):
    # ``data`` is writable in Chromium, but the bundled baseline carries an
    # unresolved setter signature for it, so declaring the setter would break
    # strict validation. Writes go through the identical ``nodeValue`` /
    # ``textContent`` setters until the baseline is regenerated.
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="data", getter="get_data", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
        ),
        methods=(
            MethodSpec(name="substringData", callback="fn_substringData", length=2),
            MethodSpec(name="appendData", callback="fn_appendData", length=1),
            MethodSpec(name="insertData", callback="fn_insertData", length=2),
            MethodSpec(name="deleteData", callback="fn_deleteData", length=2),
            MethodSpec(name="replaceData", callback="fn_replaceData", length=3),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, data="", services=None, **kw): super().__init__(services=services, node_type=Node.TEXT_NODE, node_name="#text", value=str(data), **kw)
    def get_data(self): return self.nodeValue or ""
    def set_data(self, value): self.nodeValue = str(value)
    def get_length(self): return len(self.nodeValue or "")
    def fn_substringData(self, offset, count):
        start, end = int(offset), int(offset) + int(count)
        return (self.nodeValue or "")[start:end]
    def fn_appendData(self, value): self.nodeValue = (self.nodeValue or "") + str(value)
    def fn_insertData(self, offset, value):
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + str(value) + data[start:]
    def fn_deleteData(self, offset, count):
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + data[start + int(count):]
    def fn_replaceData(self, offset, count, value):
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + str(value) + data[start + int(count):]
    def _clone_shallow(self):
        # Keep the concrete Text/Comment flavour so clones expose ``data``,
        # ``textContent`` and the correct node name/type.
        return type(self)(self.nodeValue or "", services=self._services)
    def get_textContent(self): return self.nodeValue or ""
    def set_textContent(self, value): self.set_data(value)
    def outer_html(self): return escape(self.nodeValue or "")


@interface('Text', 'CharacterData')
class Text(CharacterData):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=1, has_constructor=1)
    pass


@interface('Comment', 'CharacterData')
class Comment(CharacterData):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=1, has_constructor=1)
    def __init__(self, data="", services=None, **kw): super().__init__(data, services=services, **kw); self.nodeType, self.nodeName = Node.COMMENT_NODE, "#comment"
    def outer_html(self): return "<!--" + (self.nodeValue or "") + "-->"


@interface('DocumentType', 'Node')
class DocumentType(_ChildNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="publicId", getter="get_publicId", side_effect=1),
            AttributeSpec(name="systemId", getter="get_systemId", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, name="html", services=None, **kw):
        super().__init__(services=services, node_type=Node.DOCUMENT_TYPE_NODE, node_name=name, value=None, **kw)
    def get_name(self): return self.nodeName
    def get_publicId(self): return ""
    def get_systemId(self): return ""
    def outer_html(self): return "<!DOCTYPE " + self.nodeName + ">"


# HTML tag implementations live in ``dom.elements``. Import them after the
# core node classes exist to preserve the historical ``dom.model`` exports.
from .elements import (
    Element, HTMLElement, HTML_CLASSES, HTMLImageElement, HTMLScriptElement,
    HTMLLinkElement, HTMLStyleElement, HTMLMetaElement, HTMLTemplateElement,
    HTMLIFrameElement,
)


@interface('Document', 'Node')
class Document(_ParentNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, has_constructor=1,
        attributes=(
            AttributeSpec(name="URL", getter="get_URL", side_effect=1),
            AttributeSpec(name="documentURI", getter="get_URL", side_effect=1),
            AttributeSpec(name="documentElement", getter="get_documentElement", side_effect=1),
            AttributeSpec(name="body", getter="get_body", side_effect=1),
            AttributeSpec(name="head", getter="get_head", side_effect=1),
            AttributeSpec(name="defaultView", getter="get_defaultView", side_effect=1),
            AttributeSpec(name="childNodes", getter="get_childNodes", side_effect=1),
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="hidden", getter="get_hidden", side_effect=1),
            AttributeSpec(name="visibilityState", getter="get_visibilityState", side_effect=1),
            AttributeSpec(name="cookie", getter="get_cookie", setter="set_cookie"),
            AttributeSpec(name="all", getter="get_all", side_effect=1),
        ),
        methods=(
            MethodSpec(name="createElement", callback="fn_createElement", length=1),
            MethodSpec(name="createTextNode", callback="fn_createTextNode", length=1),
            MethodSpec(name="createComment", callback="fn_createComment", length=1),
            MethodSpec(name="createDocumentFragment", callback="fn_createDocumentFragment"),
            MethodSpec(name="createAttribute", callback="fn_createAttribute", length=1),
            MethodSpec(name="getElementById", callback="fn_getElementById", length=1, side_effect=1),
            MethodSpec(name="getElementsByTagName", callback="fn_getElementsByTagName", length=1, side_effect=1),
            MethodSpec(name="getElementsByClassName", callback="fn_getElementsByClassName", length=1, side_effect=1),
            MethodSpec(name="getElementsByTagNameNS", callback="fn_getElementsByTagNameNS", length=2, side_effect=1),
            MethodSpec(name="querySelector", callback="fn_querySelector", length=1, side_effect=1),
            MethodSpec(name="querySelectorAll", callback="fn_querySelectorAll", length=1, side_effect=1),
        ),
    )
    def __init__(self, services=None, url="about:blank", ready_state="complete", **kw):
        super().__init__(services=services, node_type=Node.DOCUMENT_NODE, node_name="#document", **kw)
        self.url = url; self._parser = None; self.ready_state = str(ready_state); self.hidden = False
    def get_URL(self): return self.url
    def get_documentElement(self): return next((x for x in self._children if isinstance(x, Element) and x.localName == "html"), pyv8.Null)
    def get_body(self):
        html = self.get_documentElement()
        if html is not pyv8.Null: return html.fn_querySelector("body")
        return next((x for x in self._walk() if isinstance(x, Element) and x.localName == "body"),
                    next((x for x in self._children if isinstance(x, Element)), pyv8.Null))
    def get_head(self):
        html = self.get_documentElement()
        if html is not pyv8.Null: return html.fn_querySelector("head")
        return next((x for x in self._walk() if isinstance(x, Element) and x.localName == "head"), pyv8.Null)
    def get_defaultView(self):
        if self._services is None:
            return pyv8.Null
        from ..bom.window import Window
        target = self._services.document_services.window
        return Window._accessor_window(target)._view(target)
    def get_readyState(self): return self.ready_state
    def get_hidden(self): return self.hidden
    def get_visibilityState(self): return "hidden" if self.hidden else "visible"
    def _cookie_jar(self):
        return getattr(getattr(self._services, "context_services", None), "cookie_jar", None)
    def get_cookie(self):
        context = self._cookie_jar()
        return context.document_for(self.url) if context is not None else ""
    def set_cookie(self, value):
        context = self._cookie_jar()
        if context is not None:
            context.set_from_document(self.url, value)
    def get_all(self):
        # One cached live view per document, like Chromium: the object identity
        # is stable while the element list is always recomputed.
        value = getattr(self, "_all_view", None)
        if value is None:
            self._materialize_interface("HTMLAllCollection")
            value = self._all_view = HTMLAllCollection(owner=self, getter=self._all_elements)
        return value
    def _all_elements(self):
        return [x for x in self._walk() if isinstance(x, Element)]
    def _materialize_interface(self, name):
        """Build a lazily declared interface before handing out one of its objects.

        ``document.all`` returns a real ``HTMLAllCollection``, so the native
        template has to exist even though the interface is not part of the
        eager startup set.
        """
        realm = getattr(getattr(self, "_services", None), "realm_services", None)
        if realm is None or realm.interface_materialized(name):
            return
        realm.materialize_interface(name)
    def fn_createElement(self, name): return self._new_element(str(name))
    def fn_createTextNode(self, value): return Text(str(value), services=self._services)
    def fn_createComment(self, value): return Comment(str(value), services=self._services)
    def fn_createDocumentFragment(self): return DocumentFragment(services=self._services)
    def fn_createAttribute(self, name): return Attr(self._validate_name(name), "", services=self._services)
    def fn_getElementById(self, value): return next((x for x in self._walk() if isinstance(x, Element) and x.get_id() == str(value)), pyv8.Null)
    def fn_getElementsByTagName(self, value):
        value = str(value).lower()
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._walk() if isinstance(x, Element) and (value == "*" or x.localName == value)])
    def fn_getElementsByClassName(self, value):
        names = set(str(value).split())
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._walk() if isinstance(x, Element) and names.issubset(set(x.get_className().split()))])
    def fn_getElementsByTagNameNS(self, namespace, local_name):
        # Only the HTML namespace is modelled, so a foreign namespace simply
        # matches nothing instead of silently ignoring the argument.
        if namespace not in (None, pyv8.Null, pyv8.Undefined, "", "*", XHTML_NAMESPACE):
            return HTMLCollection(owner=self, getter=lambda: [])
        return self.fn_getElementsByTagName(local_name)
    def fn_querySelector(self, selector): return self.query_selector(selector, self)
    def fn_querySelectorAll(self, selector): return self.query_selector_all(selector, self)
    def _validate_name(self, name):
        value = str(name)
        if not _VALID_NAME_RE.match(value):
            raise InvalidCharacterError("The qualified name provided is invalid.")
        return value
    def _new_element(self, tag):
        tag = self._validate_name(tag)
        return HTML_CLASSES.get("HTML" + tag.capitalize() + "Element", HTML_CLASSES["HTMLUnknownElement"])(tag, services=self._services)
    def _walk(self, root=None):
        """Legacy spelling of :meth:`Node._descendants` kept for callers."""
        return self._descendants() if root is None else root._descendants()
    def query_selector(self, selector, root):
        from .selectors import select
        return next(iter(select(root, selector)), pyv8.Null)
    def query_selector_all(self, selector, root):
        from .selectors import select
        return list(select(root, selector))
    def matches(self, node, selector):
        from .selectors import matches
        return matches(node, selector)
    def _parse_fragment(self, html, parent):
        from .parser import parse_fragment
        parse_fragment(html, self._services, parent)
    def _parse_one(self, html):
        tmp = Document(services=self._services, url=self.url)
        self._parse_fragment(html, tmp)
        return next(iter(tmp._children), Text("", services=self._services))
