"""The core DOM node types: ``Node``, ``DocumentFragment`` and ``Attr``.

``Node`` owns the invariant tree algorithms (adoption, insertion, cloning,
comparison) that every other node type builds on.  References to the concrete
``Element``/``Document``/``Text`` classes are resolved inside the handful of
members that need them, because those modules sit above this one.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import V8Attribute

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    dictionary,
    dom_string,
    interface_value,
    is_nullish,
    missing_error,
)
from ...foundation.errors import HierarchyRequestError, NotFoundError, V8TypeError
from .collections import NodeList
from .events import EventTarget
from .mixins import _ParentNodeMixin
from .utils import XHTML_NAMESPACE, is_element


def _is_style_element(node):
    """Whether *node* is a ``<style>`` element.

    ``nodeType`` is checked first because it is a plain attribute on every node,
    while asking a text node for ``localName`` would fall through to the
    ``__getattr__`` that raises.  This runs once per DOM mutation, so it has to
    stay this cheap.
    """
    return getattr(node, "nodeType", None) == Node.ELEMENT_NODE and node.localName == "style"


def _contains_style(node):
    """Whether *node* is a ``<style>`` element or holds one.

    Iterative rather than recursive so a deep subtree cannot exhaust the stack,
    and it allocates nothing until it meets a node that has children.  Parsing
    adopts each element before its children arrive, so the walk is a single step
    on that path; only a subtree inserted whole costs more.
    """
    if _is_style_element(node):
        return True
    children = getattr(node, "_children", ())
    if not children:
        return False
    pending = list(children)
    while pending:
        current = pending.pop()
        if _is_style_element(current):
            return True
        pending.extend(getattr(current, "_children", ()))
    return False


def _bump_style_version(node):
    """Note that the document's ``<style>`` derived data may have changed.

    Layout caches the parsed sheet on the document (see
    :func:`dom.layout.document_rules`), so a mutation that can change what a
    ``<style>`` holds has to invalidate it: inserting, moving or removing one --
    whole, or as part of a subtree built elsewhere -- and replacing or editing
    the text inside it.  Callers pass the node the mutation touched, before they
    detach it, which is enough because a text node reports the ``<style>`` it
    sits in through its parent.  Everything else, which is the overwhelming
    majority of DOM mutations, costs one ``localName`` read.

    The walk is bounded by the mutated node's own subtree, and on the parse path
    it is a single step: elements are adopted before their children arrive.
    """
    parent = getattr(node, "parentNode", None)
    if not (_is_style_element(parent) or _contains_style(node)):
        return
    document = getattr(node, "ownerDocument", None)
    if document is not None:
        document.style_version = getattr(document, "style_version", 0) + 1


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
            AttributeSpec(name="nodeType", getter="get_nodeType", property_flags=V8Attribute.DontEnum, side_effect=1),
            AttributeSpec(name="nodeName", getter="get_nodeName", side_effect=1),
            AttributeSpec(name="baseURI", getter="get_baseURI", side_effect=1),
            AttributeSpec(name="isConnected", getter="get_isConnected", side_effect=1),
            AttributeSpec(name="ownerDocument", getter="get_ownerDocument", side_effect=1),
            AttributeSpec(name="parentNode", getter="get_parentNode", side_effect=1),
            AttributeSpec(name="parentElement", getter="get_parentElement", side_effect=1),
            AttributeSpec(name="childNodes", getter="get_childNodes", side_effect=1),
            AttributeSpec(name="firstChild", getter="get_firstChild", side_effect=1),
            AttributeSpec(name="lastChild", getter="get_lastChild", side_effect=1),
            AttributeSpec(name="previousSibling", getter="get_previousSibling", side_effect=1),
            AttributeSpec(name="nextSibling", getter="get_nextSibling", side_effect=1),
            AttributeSpec(name="textContent", getter="get_textContent", setter="set_textContent"),
            AttributeSpec(name="nodeValue", getter="get_nodeValue", setter="set_nodeValue"),
        ),
        methods=(
            MethodSpec(name="appendChild", callback="fn_appendChild", length=1),
            MethodSpec(name="insertBefore", callback="fn_insertBefore", length=2),
            MethodSpec(name="removeChild", callback="fn_removeChild", length=1),
            MethodSpec(name="replaceChild", callback="fn_replaceChild", length=2),
            MethodSpec(name="cloneNode", callback="fn_cloneNode"),
            MethodSpec(name="contains", callback="fn_contains", length=1, side_effect=1),
            MethodSpec(name="hasChildNodes", callback="fn_hasChildNodes", side_effect=1),
            MethodSpec(name="isEqualNode", callback="fn_isEqualNode", length=1),
            MethodSpec(name="compareDocumentPosition", callback="fn_compareDocumentPosition", length=1),
            MethodSpec(name="getRootNode", callback="fn_getRootNode"),
            MethodSpec(name="isDefaultNamespace", callback="fn_isDefaultNamespace", length=1),
            MethodSpec(name="isSameNode", callback="fn_isSameNode", length=1),
            MethodSpec(name="lookupNamespaceURI", callback="fn_lookupNamespaceURI", length=1),
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
        return self.parentNode if is_element(self.parentNode) else None
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
        from .document import Document

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
        from .character_data import Text

        if not is_nullish(value):
            value = dom_string(value)
        self._replace_children(())
        # Replacing with the empty string leaves no child node behind (the
        # spec removes a text node whose data is empty after replacement).
        if value is not None and str(value) != "":
            self.append_child(Text(str(value), services=self._services))
    def get_nodeValue(self): return self.nodeValue if self.nodeValue is not None else pyv8.Null
    def set_nodeValue(self, value):
        if not is_nullish(value):
            value = dom_string(value)
        self.nodeValue = value
        _bump_style_version(self)

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
        _bump_style_version(node)
        services = getattr(self, "_services", None)
        document_services = getattr(services, "document_services", None)
        if document_services is not None and not document_services.parsing:
            callback = getattr(document_services, "node_adopted", None)
            if callback is not None: callback(node)
        return node

    def _replace_children(self, nodes):
        for child in self._children:
            _bump_style_version(child)
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
    def fn_appendChild(self, node=MISSING, *_extra):
        if node is MISSING:
            raise missing_error('Node', 'appendChild', 1, 0, constructor=False)
        node = interface_value(node, 'Node', 'Node', 'appendChild', 1, nullable=False)
        return self.append_child(node)
    def fn_insertBefore(self, node=MISSING, reference=MISSING, *_extra):
        if node is MISSING:
            raise missing_error('Node', 'insertBefore', 2, 0, constructor=False)
        if reference is MISSING:
            raise missing_error('Node', 'insertBefore', 2, 1, constructor=False)
        node = interface_value(node, 'Node', 'Node', 'insertBefore', 1, nullable=False)
        reference = interface_value(reference, 'Node', 'Node', 'insertBefore', 2, nullable=True)
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
    def fn_removeChild(self, node=MISSING, *_extra):
        if node is MISSING:
            raise missing_error('Node', 'removeChild', 1, 0, constructor=False)
        node = interface_value(node, 'Node', 'Node', 'removeChild', 1, nullable=False)
        if node not in self._children: raise NotFoundError("The node to be removed is not a child.")
        _bump_style_version(node)
        self._children.remove(node); node.parentNode = None; return node
    def fn_replaceChild(self, new=MISSING, old=MISSING, *_extra):
        if new is MISSING:
            raise missing_error('Node', 'replaceChild', 2, 0, constructor=False)
        if old is MISSING:
            raise missing_error('Node', 'replaceChild', 2, 1, constructor=False)
        new = interface_value(new, 'Node', 'Node', 'replaceChild', 1, nullable=False)
        old = interface_value(old, 'Node', 'Node', 'replaceChild', 2, nullable=False)
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
    def fn_cloneNode(self, deep=False, *_extra):
        if deep is pyv8.Undefined:
            deep = False
        if deep is not pyv8.Undefined:
            deep = boolean(deep)
        clone = self._clone_shallow()
        if deep:
            for child in self._children: clone.append_child(child.fn_cloneNode(True))
        return clone
    def _clone_shallow(self): return Node(services=self._services, node_type=self.nodeType, node_name=self.nodeName, value=self.nodeValue)
    def fn_compareDocumentPosition(self, other=MISSING, *_extra):
        if other is MISSING:
            raise missing_error('Node', 'compareDocumentPosition', 1, 0, constructor=False)
        other = interface_value(other, 'Node', 'Node', 'compareDocumentPosition', 1, nullable=False)
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

    def fn_getRootNode(self, options=None, *_extra):
        options = dictionary(options)
        boolean(options.get("composed", False))
        return self.root_node()

    def fn_isSameNode(self, other=MISSING, *_extra):
        if other is MISSING:
            raise missing_error('Node', 'isSameNode', 1, 0, constructor=False)
        other = interface_value(other, 'Node', 'Node', 'isSameNode', 1, nullable=True)
        return other is self

    def fn_isDefaultNamespace(self, namespace=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Node', 'isDefaultNamespace', 1, 0, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        return namespace == XHTML_NAMESPACE

    def fn_lookupNamespaceURI(self, prefix=MISSING, *_extra):
        if prefix is MISSING:
            raise missing_error('Node', 'lookupNamespaceURI', 1, 0, constructor=False)
        if not is_nullish(prefix):
            prefix = dom_string(prefix)
        if prefix in (None, pyv8.Null, pyv8.Undefined, ""):
            return XHTML_NAMESPACE
        return pyv8.Null

    def fn_lookupPrefix(self, namespace=MISSING, *_extra):
        if namespace is MISSING:
            raise missing_error('Node', 'lookupPrefix', 1, 0, constructor=False)
        if not is_nullish(namespace):
            namespace = dom_string(namespace)
        return pyv8.Null

    def fn_normalize(self, *_extra):
        """Merge adjacent text nodes and drop empty ones, recursively."""
        from .character_data import Text

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

    def fn_contains(self, other=MISSING, *_extra):
        if other is MISSING:
            raise missing_error('Node', 'contains', 1, 0, constructor=False)
        other = interface_value(other, 'Node', 'Node', 'contains', 1, nullable=True)
        return other is self or any(c.fn_contains(other) for c in self._children)
    def fn_hasChildNodes(self, *_extra):
        return bool(self._children)
    def fn_isEqualNode(self, other=MISSING, *_extra):
        if other is MISSING:
            raise missing_error('Node', 'isEqualNode', 1, 0, constructor=False)
        other = interface_value(other, 'Node', 'Node', 'isEqualNode', 1, nullable=True)
        return isinstance(other, Node) and self.outer_html() == other.outer_html()
    def outer_html(self): return self.get_textContent()


@interface('DocumentFragment', 'Node')
class DocumentFragment(_ParentNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="querySelector", callback="fn_querySelector", length=1, side_effect=1),
            MethodSpec(name="querySelectorAll", callback="fn_querySelectorAll", length=1, side_effect=1),
        ),
        constructor_behavior=1, has_constructor=1,
    )
    def __init__(self, *_extra, services=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services, node_type=Node.DOCUMENT_FRAGMENT_NODE, node_name="#document-fragment", **kw)
    def _document(self):
        """The document the fragment belongs to, when it has one.

        The selector engine lives above the node model, so a fragment asks its
        document for the match, exactly like :class:`Element` does; a detached
        fragment without services matches nothing.
        """
        return getattr(getattr(self._services, "document_services", None), "document", None)
    def fn_querySelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('DocumentFragment', 'querySelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        document = self._document()
        return document.query_selector(selector, self) if document is not None else pyv8.Null
    def fn_querySelectorAll(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('DocumentFragment', 'querySelectorAll', 1, 0, constructor=False)
        selector = dom_string(selector)
        document = self._document()
        return document.query_selector_all(selector, self) if document is not None else []
    def _clone_shallow(self): return DocumentFragment(services=self._services)


@interface('Attr', 'Node')
class Attr(Node):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="value", getter="get_value", setter="set_value"),
            AttributeSpec(name="specified", getter="get_specified", side_effect=1),
            AttributeSpec(name="namespaceURI", getter="get_namespaceURI", side_effect=1),
            AttributeSpec(name="prefix", getter="get_prefix", side_effect=1),
            AttributeSpec(name="localName", getter="get_localName", side_effect=1),
            AttributeSpec(name="ownerElement", getter="get_ownerElement", side_effect=1),
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
    def set_value(self, value):
        value = dom_string(value)
        self.nodeValue = str(value)
    def get_specified(self): return True
    # HTML attributes carry no namespace, so both namespace accessors are null.
    def get_namespaceURI(self): return pyv8.Null
    def get_prefix(self): return pyv8.Null
    def get_localName(self): return self.nodeName
    def get_ownerElement(self): return self.ownerElement or pyv8.Null
