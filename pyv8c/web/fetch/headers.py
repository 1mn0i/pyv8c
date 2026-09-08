"""Fetch header collection."""
from pyv8 import IdlSpec, MethodSpec

import pyv8



from ...engine.registry import interface


@interface('Headers', None)
class Headers:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="get", callback="fn_get", length=1, side_effect=1),
            MethodSpec(name="set", callback="fn_set", length=2),
            MethodSpec(name="append", callback="fn_append", length=2),
            MethodSpec(name="delete", callback="fn_delete", length=1),
            MethodSpec(name="has", callback="fn_has", length=1, side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, init=None, **_):
        self._data = {}
        if isinstance(init, Headers):
            self._data.update(init._data)
        elif isinstance(init, dict):
            for key, value in init.items():
                self.fn_set(key, value)

    def fn_get(self, key):
        return self._data.get(str(key).lower(), pyv8.Null)

    def fn_set(self, key, value):
        self._data[str(key).lower()] = str(value)

    def fn_append(self, key, value):
        key = str(key).lower()
        self._data[key] = self._data.get(key, "") + (
            ", " if key in self._data else ""
        ) + str(value)

    def fn_delete(self, key):
        self._data.pop(str(key).lower(), None)

    def fn_has(self, key):
        return str(key).lower() in self._data


__all__ = ["Headers"]
