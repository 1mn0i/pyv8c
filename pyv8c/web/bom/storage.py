"""``Storage``: the key/value store behind the two storage properties.

The table is per instance, so ``localStorage`` and ``sessionStorage`` never share
entries and each realm gets its own pair.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from ..dom.utils import OBJECT_PROTOTYPE_NAMES
from .utils import settled

@interface('StorageBucket', None)
class StorageBucket:
    """A bucket that holds nothing: a render keeps no storage between loads."""

    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="persist", callback="fn_persist", length=0, side_effect=1),
            MethodSpec(name="persisted", callback="fn_persisted", length=0, side_effect=1),
            MethodSpec(name="estimate", callback="fn_estimate", length=0, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="StorageBucket",
        has_constructor=1,
    )

    def __init__(self, name="", services=None, **_):
        self._name = str(name)
        self._services = services

    def fn_persist(self, *_extra): return settled(self._services, value=False)
    def fn_persisted(self, *_extra): return settled(self._services, value=False)
    def fn_estimate(self, *_extra):
        return settled(self._services, value={"usage": 0, "quota": 0})


@interface('StorageBucketManager', None)
class StorageBucketManager:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="keys", callback="fn_keys", length=0, side_effect=1),
            MethodSpec(name="open", callback="fn_open", length=2, side_effect=1),
            MethodSpec(name="delete", callback="fn_delete", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="StorageBucketManager",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services

    def fn_keys(self, *_extra): return settled(self._services, value=[])
    def fn_open(self, name=MISSING, options=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('StorageBucketManager', 'open', 1, 0, constructor=False)
        return settled(self._services, value=StorageBucket(name=dom_string(name),
                                                           services=self._services))
    def fn_delete(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('StorageBucketManager', 'delete', 1, 0, constructor=False)
        return settled(self._services, value=None)


__all__ = ["Storage", "StorageBucket", "StorageBucketManager"]


@interface('Storage', None)
class Storage:
    """One origin's key/value store; entries are also the object's named
    properties, and a key spelled like a prototype member stays that member, as
    in Chromium.
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
