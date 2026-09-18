"""``Storage``: the key/value store behind the two storage properties.

The table is per instance, so ``localStorage`` and ``sessionStorage`` never
share entries and each realm gets its own pair.
"""

import v8n
from v8n import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error

__all__ = ["Storage"]


@interface('Storage', None)
class Storage:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="getItem", callback="fn_getItem", length=1, side_effect=1),
            MethodSpec(name="setItem", callback="fn_setItem", length=2),
            MethodSpec(name="removeItem", callback="fn_removeItem", length=1),
            MethodSpec(name="clear", callback="fn_clear"),
            MethodSpec(name="key", callback="fn_key", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, **_): self._data = {}
    def get_length(self): return len(self._data)
    def fn_getItem(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'getItem', 1, 0, constructor=False)
        key = dom_string(key)
        return self._data.get(str(key), v8n.Null)
    def fn_setItem(self, key=MISSING, value=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'setItem', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('Storage', 'setItem', 2, 1, constructor=False)
        key = dom_string(key)
        value = dom_string(value)
        self._data[str(key)] = str(value)
    def fn_removeItem(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'removeItem', 1, 0, constructor=False)
        key = dom_string(key)
        self._data.pop(str(key), None)
    def fn_clear(self, *_extra):
        self._data.clear()
    def fn_key(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('Storage', 'key', 1, 0, constructor=False)
        index = integer(index)
        return list(self._data)[index] if index < len(self._data) else v8n.Null
