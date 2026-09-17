"""Web IDL mixin interfaces shared across the DOM layer.

``Element``, ``CharacterData``, ``DocumentType``, ``DocumentFragment`` and
``Document`` compose the node model out of the first three mixins.  Every
legacy collection -- ``NodeList``, ``HTMLCollection``, ``HTMLAllCollection``,
``DOMTokenList``, ``DOMStringList``, ``FileList`` and the plugin arrays --
reuses :class:`ReadOnlyIndexedPropertyMixin` for its indexed-property
interceptor, and the collections that also expose named properties share
:class:`LegacyNamedPropertyMixin`.  Each mixin carries only the members its own
IDL interface declares.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import V8Attribute

from ...foundation.errors import LegacyPropertyError
from .utils import NAMED_PROPERTY_ATTRIBUTES, OBJECT_PROTOTYPE_NAMES, as_nodes, is_element


class _NonDocumentTypeChildNodeMixin:
    """``previousElementSibling`` / ``nextElementSibling``.

    Included by :class:`Element` and :class:`CharacterData`; document type
    nodes are excluded, matching the ``NonDocumentTypeChildNode`` mixin.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="previousElementSibling", getter="get_previousElementSibling", side_effect=1),
            AttributeSpec(name="nextElementSibling", getter="get_nextElementSibling", side_effect=1),
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
            if is_element(siblings[index]):
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
            AttributeSpec(name="firstElementChild", getter="get_firstElementChild", side_effect=1),
            AttributeSpec(name="lastElementChild", getter="get_lastElementChild", side_effect=1),
            AttributeSpec(name="childElementCount", getter="get_childElementCount", side_effect=1),
        ),
        methods=(
            MethodSpec(name="append", callback="fn_append"),
            MethodSpec(name="prepend", callback="fn_prepend"),
            MethodSpec(name="replaceChildren", callback="fn_replaceChildren"),
        ),
    )

    def _element_children(self):
        return [child for child in self._children if is_element(child)]

    def get_firstElementChild(self):
        children = self._element_children()
        return children[0] if children else pyv8.Null
    def get_lastElementChild(self):
        children = self._element_children()
        return children[-1] if children else pyv8.Null
    def get_childElementCount(self): return len(self._element_children())

    def fn_append(self, *values):
        for node in as_nodes(values, self):
            self.append_child(node)
    def fn_prepend(self, *values):
        for index, node in enumerate(as_nodes(values, self)):
            self._adopt_child(node, index)
    def fn_replaceChildren(self, *values):
        nodes = as_nodes(values, self)
        self._replace_children(())
        for node in nodes:
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
        nodes = as_nodes(values, self)
        parent = self.parentNode
        if parent is None:
            return
        for node in nodes:
            parent._adopt_child(node, parent._children.index(self))

    def fn_after(self, *values):
        nodes = as_nodes(values, self)
        parent = self.parentNode
        if parent is None:
            return
        for node in nodes:
            parent._adopt_child(node, parent._children.index(self) + 1)

    def fn_replaceWith(self, *values):
        parent = self.parentNode
        nodes = as_nodes(values, self)
        if parent is None:
            return
        index = parent._children.index(self)
        self.fn_remove()
        for offset, node in enumerate(nodes):
            parent._adopt_child(node, index + offset)

    def fn_remove(self, *_extra):
        parent = self.parentNode
        if parent is not None:
            parent.fn_removeChild(self)


class ReadOnlyIndexedPropertyMixin:
    """Implement V8's readonly indexed-property interceptor contract.

    Subclasses provide :meth:`_indexed_items`; this mixin supplies numeric
    lookup, descriptor flags, deletion, and assignment semantics for Web IDL
    collection objects in any browser subsystem.
    """

    __v8_idl_spec__ = pyv8.IdlSpec(
        index_get="__v8_index_get__",
        index_set="__v8_index_set__",
        index_query="__v8_index_query__",
        index_del="__v8_index_del__",
        index_enum="__v8_index_enum__",
    )

    def _indexed_items(self):
        raise NotImplementedError

    def _item_at(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError, OverflowError):
            return pyv8.Null
        items = self._indexed_items()
        return items[index] if 0 <= index < len(items) else pyv8.Null

    def _has_index(self, index):
        return self._item_at(index) is not pyv8.Null

    def _interface_name(self):
        return getattr(getattr(type(self), "__idl_spec__", None), "name", type(self).__name__)

    def __v8_index_get__(self, index):
        value = self._item_at(index)
        return None if value is pyv8.Null else value

    def __v8_index_set__(self, index, value):
        if self._has_index(index):
            raise LegacyPropertyError.indexed(
                "set", index, self._interface_name(),
                "Indexed property setter is not supported.",
            )
        return None

    def __v8_index_query__(self, index):
        return V8Attribute.ReadOnly if self._has_index(index) else None

    def __v8_index_del__(self, index):
        if self._has_index(index):
            raise LegacyPropertyError.indexed(
                "delete", index, self._interface_name(),
                "Indexed property deleter is not supported.",
            )
        return None

    def __v8_index_enum__(self):
        return len(self._indexed_items())


class LegacyNamedPropertyMixin:
    """V8's read-only legacy named-property interceptor contract.

    Subclasses provide :meth:`_supported_names` and :meth:`_named_item`.  The
    named properties are read-only and never shadow a prototype member, so
    assignment and deletion raise :class:`LegacyPropertyError` -- which V8
    swallows silently outside strict mode, exactly like Chromium.

    Always composed with :class:`ReadOnlyIndexedPropertyMixin`, which supplies
    :meth:`_interface_name` for the error messages.
    """

    __v8_idl_spec__ = IdlSpec(
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
    )

    #: Named properties never shadow these prototype members.
    _prototype_names = OBJECT_PROTOTYPE_NAMES | {"length", "item", "namedItem"}

    def _supported_names(self):
        raise NotImplementedError

    def _named_item(self, name):
        raise NotImplementedError

    def _visible_names(self):
        return [name for name in self._supported_names() if name not in self._prototype_names]

    def __v8_name_get__(self, name):
        if not isinstance(name, str) or name not in self._visible_names():
            return None
        value = self._named_item(name)
        return None if value is pyv8.Null else value

    def __v8_name_set__(self, name, value):
        if isinstance(name, str) and name in self._visible_names():
            raise LegacyPropertyError.named(
                "set", name, self._interface_name(),
                "Named property setter is not supported.",
            )
        return None

    def __v8_name_query__(self, name):
        if not isinstance(name, str):
            return None
        return NAMED_PROPERTY_ATTRIBUTES if name in self._visible_names() else None

    def __v8_name_del__(self, name):
        if isinstance(name, str) and name in self._visible_names():
            raise LegacyPropertyError.named(
                "delete", name, self._interface_name(),
                "Named property deleter is not supported.",
            )
        return None

    def __v8_name_enum__(self):
        return self._visible_names()
