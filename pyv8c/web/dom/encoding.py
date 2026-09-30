"""TextEncoder and TextDecoder: the one encoding every page can rely on.

The other labels the standard names are not modelled, and constructing with one of
them raises ``RangeError`` the way an unknown label does.
"""
from __future__ import annotations

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dictionary, is_nullish, missing_error, usv_string
from ...foundation.errors import V8TypeError, dom_exception

__all__ = ["TextDecoder", "TextEncoder"]

#: The labels the standard files under utf-8.
_UTF8_LABELS = frozenset((
    "unicode-1-1-utf-8", "unicode11utf8", "unicode20utf8", "utf-8", "utf8", "x-unicode20utf8",
))
_BOM = b"\xef\xbb\xbf"


def _view(value):
    """The ArrayBufferView a caller passed, or ``None`` for anything else."""
    return value if isinstance(value, pyv8.JSTypeArray) else None


def _bytes_of(value):
    """The bytes of an ArrayBufferView, or ``None`` when the value is not one."""
    view = _view(value)
    if view is None:
        return bytes(value) if isinstance(value, (bytes, bytearray)) else None
    return bytes(int(view[index]) for index in range(int(view._get("byteLength"))))


def _code_units(character):
    """How many UTF-16 code units one code point occupies."""
    return 2 if ord(character) > 0xFFFF else 1


@interface('TextEncoder', None)
class TextEncoder:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="encoding", getter="get_encoding", side_effect=1),),
        methods=(
            MethodSpec(name="encode", callback="fn_encode"),
            MethodSpec(name="encodeInto", callback="fn_encodeInto", length=2),
        ),
        constructor_behavior=1,
        to_string_tag="TextEncoder",
        has_constructor=1,
    )

    def __init__(self, *_extra, **_):
        # Chrome's constructor takes no arguments; the binding still passes the realm.
        pass

    def get_encoding(self): return "utf-8"

    def fn_encode(self, source=pyv8.Undefined, *_extra):
        text = "" if source is pyv8.Undefined else usv_string(source)
        return pyv8.JSTypeArray(list(text.encode("utf-8")), pyv8.ArrayType.Uint8Array)

    def fn_encodeInto(self, source=MISSING, destination=MISSING, *_extra):
        if source is MISSING:
            raise missing_error('TextEncoder', 'encodeInto', 2, 0, constructor=False)
        if destination is MISSING:
            raise missing_error('TextEncoder', 'encodeInto', 2, 1, constructor=False)
        target = _view(destination)
        if target is None:
            raise V8TypeError("Failed to execute 'encodeInto' on 'TextEncoder': "
                              "parameter 2 is not of type 'Uint8Array'.")
        text = usv_string(source)
        room = int(target._get("length"))
        written, read = 0, 0
        for character in text:
            encoded = character.encode("utf-8")
            # A code point is written whole or not at all, which is what ``read`` counts.
            if written + len(encoded) > room:
                break
            for offset, byte in enumerate(encoded):
                target[written + offset] = byte
            written += len(encoded)
            read += _code_units(character)
        return {"read": read, "written": written}


@interface('TextDecoder', None)
class TextDecoder:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="encoding", getter="get_encoding", side_effect=1),
            AttributeSpec(name="fatal", getter="get_fatal", side_effect=1),
            AttributeSpec(name="ignoreBOM", getter="get_ignoreBOM", side_effect=1),
        ),
        methods=(MethodSpec(name="decode", callback="fn_decode"),),
        constructor_behavior=1,
        to_string_tag="TextDecoder",
        has_constructor=1,
    )

    def __init__(self, label="utf-8", options=None, *_extra, **_):
        label = str(label).strip().lower() if not is_nullish(label) else "utf-8"
        if label not in _UTF8_LABELS:
            raise dom_exception("RangeError",
                                "Failed to construct 'TextDecoder': The encoding label"
                                f" provided ('{label}') is invalid.")
        options = {} if is_nullish(options) else dictionary(options, name="TextDecoderOptions")
        self._fatal = bool(options.get("fatal", False))
        self._ignore_bom = bool(options.get("ignoreBOM", False))

    def get_encoding(self): return "utf-8"
    def get_fatal(self): return self._fatal
    def get_ignoreBOM(self): return self._ignore_bom

    def fn_decode(self, input=pyv8.Undefined, options=None, *_extra):
        data = b"" if input is pyv8.Undefined or is_nullish(input) else _bytes_of(input)
        if data is None:
            raise V8TypeError("Failed to execute 'decode' on 'TextDecoder': "
                              "parameter 1 is not of type 'BufferSource'.")
        if not self._ignore_bom and data.startswith(_BOM):
            data = data[len(_BOM):]
        try:
            return data.decode("utf-8", "strict" if self._fatal else "replace")
        except UnicodeDecodeError as error:
            raise V8TypeError("Failed to execute 'decode' on 'TextDecoder': "
                              "The encoded data was not valid.") from error
