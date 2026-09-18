"""Blob data type and byte conversion helpers."""
import v8n
from v8n import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import dictionary, dom_string, enum_value, integer, is_nullish, sequence, usv_string
from ...foundation.errors import V8TypeError


def bytes_from_value(value):
    if isinstance(value, Blob):
        return value._data
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, v8n.Value):
        # A JavaScript array materialises into a list and is handled below.  A
        # typed array does not: the value layer keeps a binary view as a handle
        # rather than expanding it, so its bytes are read one element at a time.
        # Materialising it here would hand back another handle and never end.
        materialised = value.to_python()
        # A materialised value is a list, a dict or a string -- never a handle --
        # so this is the test that says whether anything was expanded.  Comparing
        # the object with `is` would not: materialising a value the layer keeps as
        # a reference hands back a *new* wrapper for it, and the branch would take
        # itself again forever.
        if not isinstance(materialised, v8n.Value):
            return bytes_from_value(materialised)
        length = value.get("length")
        if not isinstance(length, int) or isinstance(length, bool):
            return None
        return bytes(int(value.get(str(index))) & 255 for index in range(length))
    if isinstance(value, (list, tuple)):
        return bytes(int(item) & 255 for item in value)
    try:
        return bytes(value)
    except (TypeError, ValueError):
        return None


@interface('Blob', None)
class Blob:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="size", getter="get_size", side_effect=1),
            AttributeSpec(name="type", getter="get_type", side_effect=1),
        ),
        methods=(
            MethodSpec(name="slice", callback="fn_slice", length=3, side_effect=1),
            MethodSpec(name="text", callback="fn_text", side_effect=1),
            MethodSpec(name="arrayBuffer", callback="fn_arrayBuffer", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, parts=v8n.Undefined, options=None, *_extra, _realm=None, **_):
        if parts is v8n.Undefined:
            parts = []
        elif is_nullish(parts) or isinstance(parts, (int, float, bool, str)):
            raise V8TypeError("Failed to construct 'Blob': The provided value cannot be converted to a sequence.")
        else:
            parts = sequence(parts, lambda part: part if isinstance(part, (Blob, bytes, bytearray, memoryview, v8n.Value)) else usv_string(part))
        if options is v8n.Undefined:
            options = None
        if options is not v8n.Undefined:
            if not is_nullish(options):
                options = dictionary(options)
        self._realm = _realm
        endings = enum_value(options.get("endings", "transparent"), ("transparent", "native")) if isinstance(options, dict) else "transparent"
        data = bytearray()
        for part in parts:
            if endings == "native" and isinstance(part, str):
                import os
                part = part.replace("\r\n", "\n").replace("\r", "\n").replace("\n", os.linesep)
            chunk = bytes_from_value(part)
            if chunk is not None:
                data.extend(chunk)
        options = options if isinstance(options, dict) else {}
        self._data = bytes(data)
        self._type = dom_string(options.get("type", "")).lower()
        if any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in self._type):
            self._type = ""

    def get_size(self):
        return len(self._data)

    def get_type(self):
        return self._type

    def fn_slice(self, start=0, end=v8n.Undefined, content_type='', *_extra):
        if start is v8n.Undefined:
            start = 0
        start = integer(start, bits=64, signed=True)
        if end is not v8n.Undefined:
            end = integer(end, bits=64, signed=True)
        if content_type is v8n.Undefined:
            content_type = ''
        content_type = dom_string(content_type)
        size = len(self._data)
        start = int(start)
        end = size if end is v8n.Undefined else int(end)
        start = max(size + start, 0) if start < 0 else min(start, size)
        end = max(size + end, 0) if end < 0 else min(end, size)
        return Blob(
            [self._data[start:max(start, end)]],
            {"type": str(content_type).lower()}, _realm=self._realm,
        )

    def fn_text(self, *_extra):
        value = self._data.decode("utf-8", "replace")
        return self._realm.realm_services.promise_resolve(value) if self._realm else value

    def fn_arrayBuffer(self, *_extra):
        return self._realm.realm_services.promise_resolve(self._data) if self._realm else self._data


__all__ = ["Blob", "bytes_from_value"]
