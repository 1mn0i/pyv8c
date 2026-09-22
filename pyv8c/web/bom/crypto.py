"""``window.crypto``: random values, and what a secure context adds.

``getRandomValues`` fills the caller's array in place.  ``randomUUID`` is
secure-context only, as it is in Chrome, and ``subtle`` is not modelled.
"""

import os
import uuid

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, missing_error


@interface('Crypto', None)
class Crypto:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="subtle", getter="get_subtle", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getRandomValues", callback="fn_getRandomValues", length=1),
            MethodSpec(name="randomUUID", callback="fn_randomUUID", length=0, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="Crypto",
        has_constructor=1,
    )

    def __init__(self, secure=False, **_):
        self._secure = bool(secure)

    def get_subtle(self):
        return pyv8.Undefined

    def fn_randomUUID(self, *_extra):
        """Secure-context only, which is where Chrome exposes it."""
        return str(uuid.uuid4()) if self._secure else pyv8.Undefined

    def fn_getRandomValues(self, array=MISSING, *_extra):
        if array is MISSING:
            raise missing_error('Crypto', 'getRandomValues', 1, 0, constructor=False)
        length = int(getattr(array, "length", 0) or 0)
        for index, byte in enumerate(os.urandom(length)):
            array[index] = byte
        return array


__all__ = ["Crypto"]
