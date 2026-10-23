"""Blob data type and byte conversion helpers."""
import pyv8

from ...idl import interface
from ...foundation.arguments import IdlArgumentError, dictionary, dom_string, enum_value, integer, is_nullish, sequence, usv_string
from ..clone import js_helper
from ..page_errors import PageTypeError


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


def _array_buffer_bytes(value):
    """Snapshot native BufferSource bytes without page property access."""
    if not isinstance(value, pyv8.JSObject):
        return None
    kind = value._buffer_kind()
    if kind is None:
        return None
    if kind == 'detached':
        raise PageTypeError('The ArrayBuffer is detached.')
    return bytes(value)


def _part_value(part):
    """A blob part as the value it contributes.

    A blob, bytes or a view are themselves, a buffer is read out of the realm that holds
    it, and anything else is the string it converts to -- which is the only reading left
    for a value that is none of those.
    """
    if isinstance(part, (Blob, bytes, bytearray, memoryview, pyv8.JSTypeArray)):
        return part
    buffer = _array_buffer_bytes(part)
    return buffer if buffer is not None else usv_string(part)


@interface('Blob', None)
class Blob:

    def __init__(self, parts=pyv8.Undefined, options=None, *_extra, _realm=None, **_):
        if parts is pyv8.Undefined:
            parts = []
        elif is_nullish(parts) or isinstance(parts, (int, float, bool, str)):
            raise PageTypeError("Failed to construct 'Blob': The provided value cannot be converted to a sequence.")
        else:
            try:
                parts = sequence(parts, _part_value)
            except IdlArgumentError as error:
                raise PageTypeError(f"Failed to construct 'Blob': {error}") from None
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

    def fn_slice(self, start=0, end=pyv8.Undefined, content_type='', *_extra):
        if start is pyv8.Undefined:
            start = 0
        start = integer(start, bits=64, signed=True)
        if end is not pyv8.Undefined:
            end = integer(end, bits=64, signed=True)
        if content_type is pyv8.Undefined:
            content_type = ''
        content_type = dom_string(content_type)
        size = len(self._data)
        start = int(start)
        end = size if end is pyv8.Undefined else int(end)
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
