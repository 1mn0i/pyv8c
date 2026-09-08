"""Blob data type and byte conversion helpers."""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

import pyv8



from ...engine.registry import interface


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

    def __init__(self, parts=None, options=None, _realm=None, **_):
        self._realm = _realm
        parts = parts if isinstance(parts, (list, tuple)) else (
            [] if parts in (None, pyv8.Null, pyv8.Undefined) else [parts]
        )
        data = bytearray()
        for part in parts:
            chunk = bytes_from_value(part)
            if chunk is not None:
                data.extend(chunk)
        options = options if isinstance(options, dict) else {}
        self._data = bytes(data)
        self._type = str(options.get("type", "")).lower()
        if any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in self._type):
            self._type = ""

    def get_size(self):
        return len(self._data)

    def get_type(self):
        return self._type

    def fn_slice(self, start=0, end=pyv8.Undefined, content_type=""):
        size = len(self._data)
        start = int(start)
        end = size if end is pyv8.Undefined else int(end)
        start = max(size + start, 0) if start < 0 else min(start, size)
        end = max(size + end, 0) if end < 0 else min(end, size)
        return Blob(
            [self._data[min(start, end):max(start, end)]],
            {"type": str(content_type).lower()}, _realm=self._realm,
        )

    def fn_text(self):
        value = self._data.decode("utf-8", "replace")
        return self._realm.realm_services.promise_resolve(value) if self._realm else value

    def fn_arrayBuffer(self):
        return self._realm.realm_services.promise_resolve(self._data) if self._realm else self._data


__all__ = ["Blob", "bytes_from_value"]
