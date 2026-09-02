"""Fetch header collection."""

import pyv8
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_methods


@interface("Headers", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Headers:
    __v8_method__ = js_methods(
        ("get", "fn_get", 1, 1), ("set", "fn_set", 2, 0),
        ("append", "fn_append", 2, 0), ("delete", "fn_delete", 1, 0),
        ("has", "fn_has", 1, 1),
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
