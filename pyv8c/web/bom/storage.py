"""``Storage``: the key/value store behind the two storage properties.

The table is per instance, so ``localStorage`` and ``sessionStorage`` never
share entries and each realm gets its own pair.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from ..dom.utils import OBJECT_PROTOTYPE_NAMES

__all__ = ["Storage"]


@interface('Storage', None)
class Storage:
    """One origin's key/value store, as ``localStorage`` and ``sessionStorage``.

    The entries are the object's named properties as well: a page may write
    ``localStorage.token = 'x'`` and read the same value back through
    ``getItem('token')``, and ``Object.keys`` lists the names.  A key spelled like one
    of the prototype's own members stays that member, which is what Chromium does.
    """

    #: Names the prototype keeps even when a key is spelled like them.
    _prototype_names = OBJECT_PROTOTYPE_NAMES | {
        "length", "key", "getItem", "setItem", "removeItem", "clear",
    }

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
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
    )
    def __init__(self, **_): self._data = {}
    def get_length(self): return len(self._data)
    def fn_getItem(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'getItem', 1, 0, constructor=False)
        key = dom_string(key)
        return self._data.get(str(key), pyv8.Null)
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
        return list(self._data)[index] if index < len(self._data) else pyv8.Null

    def __v8_name_get__(self, name):
        if not isinstance(name, str) or name in self._prototype_names:
            return None
        return self._data[name] if name in self._data else None
    def __v8_name_set__(self, name, value):
        if not isinstance(name, str) or name in self._prototype_names:
            return None
        self._data[name] = str(dom_string(value))
        return True
    def __v8_name_query__(self, name):
        if not isinstance(name, str) or name in self._prototype_names:
            return None
        return 0 if name in self._data else None
    def __v8_name_del__(self, name):
        if not isinstance(name, str) or name in self._prototype_names:
            return None
        self._data.pop(name, None)
        return True
    def __v8_name_enum__(self):
        return list(self._data)
