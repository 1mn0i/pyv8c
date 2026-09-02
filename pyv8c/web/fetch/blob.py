"""Blob data type and byte conversion helpers."""

import pyv8
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods


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


@interface("Blob", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Blob:
    __v8_attribute__ = js_attributes(
        ("size", "get_size", None, 0, 1), ("type", "get_type", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("slice", "fn_slice", 3, 1), ("text", "fn_text", 0, 1),
        ("arrayBuffer", "fn_arrayBuffer", 0, 1),
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
        return self._realm.promise_resolve(value) if self._realm else value

    def fn_arrayBuffer(self):
        return self._realm.promise_resolve(self._data) if self._realm else self._data


__all__ = ["Blob", "bytes_from_value"]
