"""DOM collection views: live node lists and token lists.

These classes deliberately have no dependency on the node model itself:
they read through owner-supplied getters, which keeps them importable by
:meth:`dom.model` without a cycle.
"""

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagExposed

from ..bindings.registry import interface, js_attributes, js_methods
from ..core.errors import V8TypeError, InvalidCharacterError


@interface("NodeList", None, constructible=False, tag="NodeList")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class NodeList:
    """A live list view. The owner remains the source of truth."""
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1))
    def __init__(self, owner=None, getter=None, values=None, **_):
        self._owner, self._getter, self._values = owner, getter, values
    def _items(self):
        items = self._getter() if self._getter else (self._values or ())
        return items if hasattr(items, "__len__") and hasattr(items, "__getitem__") else tuple(items)
    def get_length(self): return len(self._items())
    def fn_item(self, index):
        try: return self._items()[int(index)]
        except (IndexError, ValueError, TypeError): return pyv8.Null
    def __iter__(self): return iter(self._items())
    def __getitem__(self, index): return self._items()[index]
    def __len__(self): return len(self._items())
    __v8_index_get__ = lambda self, index: self.fn_item(index)
    __v8_index_query__ = lambda self, index: int(index) < len(self)
    __v8_index_enum__ = lambda self: list(range(len(self)))


@interface("HTMLCollection", None, constructible=False, tag="HTMLCollection")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLCollection(NodeList):
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))
    def fn_namedItem(self, name):
        value = str(name)
        return next((n for n in self._items() if getattr(n, "get_id", lambda: "")() == value or
                     getattr(n, "attributes", {}).get("name") == value), pyv8.Null)


@interface("DOMTokenList", None, constructible=False, tag="DOMTokenList")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class DOMTokenList:
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1), ("value", "get_value", "set_value", 0, 0))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("contains", "fn_contains", 1, 1),
                               ("add", "fn_add", 1, 0), ("remove", "fn_remove", 1, 0),
                               ("toggle", "fn_toggle", 1, 0), ("replace", "fn_replace", 2, 0))
    def __init__(self, element=None, attr="class", **_): self._element, self._attr = element, attr
    def _tokens(self): return [x for x in str(self._element.attributes.get(self._attr, "")).split() if x]
    def _validate(self, token):
        token = str(token)
        if not token: raise V8TypeError("The token provided must not be empty.")
        if any(ch.isspace() for ch in token):
            raise InvalidCharacterError("The token provided contains HTML space characters, which are not valid in tokens.")
        return token
    def _set(self, values): self._element.attributes[self._attr] = " ".join(dict.fromkeys(values))
    def get_length(self): return len(self._tokens())
    def get_value(self): return " ".join(self._tokens())
    def set_value(self, value): self._set([self._validate(token) for token in str(value).split()])
    def fn_item(self, index):
        try: return self._tokens()[int(index)]
        except (IndexError, ValueError, TypeError): return pyv8.Null
    def fn_contains(self, token): return self._validate(token) in self._tokens()
    def fn_add(self, *tokens): self._set(self._tokens() + [self._validate(x) for x in tokens])
    def fn_remove(self, *tokens): self._set([x for x in self._tokens() if x not in {self._validate(t) for t in tokens}])
    def fn_toggle(self, token, force=pyv8.Undefined):
        token = self._validate(token); present = token in self._tokens()
        if force is not pyv8.Undefined: present = not bool(force)
        if present: self.fn_remove(token); return False
        self.fn_add(token); return True
    def fn_replace(self, old, new):
        values = self._tokens(); old, new = self._validate(old), self._validate(new)
        if old not in values: return False
        self._set([new if x == old else x for x in values]); return True
    def __iter__(self): return iter(self._tokens())
