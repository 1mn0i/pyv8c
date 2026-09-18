"""Fetch header collection; conversion precedes validation and mutation."""

import v8n
from v8n import IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    byte_string,
    dictionary,
    header_name,
    header_value,
    is_nullish,
    missing_error,
    sequence,
)
from ...foundation.errors import V8TypeError


@interface('Headers', None)
class Headers:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="get", callback="fn_get", length=1, side_effect=1),
            MethodSpec(name="set", callback="fn_set", length=2),
            MethodSpec(name="append", callback="fn_append", length=2),
            MethodSpec(name="delete", callback="fn_delete", length=1),
            MethodSpec(name="has", callback="fn_has", length=1, side_effect=1),
        ), constructor_behavior=1, length=1, has_constructor=1,
    )

    def __init__(self, init=v8n.Undefined, *_extra, **_):
        self._data = {}
        if init is v8n.Undefined:
            return
        if isinstance(init, Headers):
            self._data.update(init._data)
            return
        # None is an internal absence marker; explicit JavaScript null is not.
        if init is None:
            return
        if is_nullish(init) or isinstance(init, (str, bool, int, float)):
            raise V8TypeError("Failed to construct 'Headers': The provided value is not of type '(sequence<sequence<ByteString>> or record<ByteString, ByteString>)'.")
        iterable = isinstance(init, (list, tuple))
        iterator = MISSING
        if isinstance(init, v8n.Value):
            iterator = init.iterator()
            iterable = not is_nullish(iterator)

        def convert_pair(entry):
            pair = sequence(entry, byte_string)
            if len(pair) != 2:
                raise V8TypeError("Failed to construct 'Headers': Sequence initializer must only contain pair elements.")
            return tuple(pair)

        if iterable:
            converted = sequence(init, convert_pair, iterator_method=iterator)
        else:
            converted = [(byte_string(key), byte_string(value)) for key, value in dictionary(init).items()]
        for key, value in converted:
            self.fn_append(key, value)

    def fn_get(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Headers', 'get', 1, 0)
        key = byte_string(key, prefix="Failed to execute 'get' on 'Headers': ")
        return self._data.get(header_name(key), v8n.Null)

    def fn_set(self, key=MISSING, value=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Headers', 'set', 2, 0)
        if value is MISSING:
            raise missing_error('Headers', 'set', 2, 1)
        key = byte_string(key, prefix="Failed to execute 'set' on 'Headers': ")
        value = byte_string(value, prefix="Failed to execute 'set' on 'Headers': ")
        key, value = header_name(key), header_value(value)
        self._data[key] = value

    def fn_append(self, key=MISSING, value=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Headers', 'append', 2, 0)
        if value is MISSING:
            raise missing_error('Headers', 'append', 2, 1)
        key = byte_string(key, prefix="Failed to execute 'append' on 'Headers': ")
        value = byte_string(value, prefix="Failed to execute 'append' on 'Headers': ")
        key, value = header_name(key), header_value(value)
        self._data[key] = self._data[key] + ", " + value if key in self._data else value

    def fn_delete(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Headers', 'delete', 1, 0)
        key = byte_string(key, prefix="Failed to execute 'delete' on 'Headers': ")
        self._data.pop(header_name(key), None)

    def fn_has(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Headers', 'has', 1, 0)
        key = byte_string(key, prefix="Failed to execute 'has' on 'Headers': ")
        return header_name(key) in self._data


__all__ = ["Headers"]
