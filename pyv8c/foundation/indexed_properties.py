"""Shared Web IDL indexed-property protocols independent of a DOM model."""

import pyv8
from pyv8.flag import V8Attribute

from .errors import LegacyPropertyError


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


__all__ = ["ReadOnlyIndexedPropertyMixin"]
