"""DOM collection views: live node lists and token lists.

These classes deliberately have no dependency on the node model itself:
they read through owner-supplied getters, which keeps them importable by
:meth:`dom.model` without a cycle.
"""

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagExposed, V8Attribute

from ..bindings.registry import interface, js_attributes, js_methods
from ..core.errors import V8TypeError, InvalidCharacterError, LegacyPropertyError


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


def _node_id(node):
    """The ``id`` of any node-like object, without importing the node model."""
    getter = getattr(node, "get_id", None)
    value = getter() if callable(getter) else ""
    return str(value) if value else ""


def _node_name(node):
    tag = str(getattr(node, "localName", "")).lower()
    if tag not in {"a", "button", "embed", "form", "frame", "frameset",
                   "iframe", "img", "input", "map", "meta", "object",
                   "select", "textarea"}:
        return ""
    return str(getattr(node, "attributes", {}).get("name") or "")


@interface("HTMLCollection", None, constructible=False, tag="HTMLCollection")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLCollection(NodeList):
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))
    def fn_namedItem(self, name):
        value = str(name)
        return next((n for n in self._items()
                     if _node_id(n) == value or _node_name(n) == value), pyv8.Null)


# ``HTMLAllCollection`` is declared ``[LegacyUnenumerableNamedProperties]``:
# the named properties exist and are reachable, but their descriptors are
# non-enumerable (so they are omitted by ``Object.keys`` and ``for-in``).
_ALL_NAME_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum


def _dom_string(value):
    if value is pyv8.Undefined:
        return "undefined"
    if value is pyv8.Null or value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        if value == 0:
            return "0"
        if value.is_integer():
            return str(int(value))
    return str(value)


def _array_index_name(value):
    """Return a canonical array index, or ``None`` for a normal property name."""
    if not value or any(ch < "0" or ch > "9" for ch in value) or (len(value) > 1 and value[0] == "0"):
        return None
    try:
        index = int(value)
    except ValueError:
        return None
    return index if index < 2 ** 32 - 1 else None


@interface("HTMLAllCollection", None, constructible=False, tag="HTMLAllCollection")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLAllCollection:
    """The legacy ``document.all`` view: undetectable, callable and live.

    Chromium marks the interface undetectable, so ``typeof document.all`` is
    ``"undefined"`` and the object is falsy, while every other operation still
    resolves against the document element list: indexed lookup, named lookup
    by ``id`` or ``name``, ``item()``/``namedItem()`` and the legacy call
    form ``document.all(name)``.

    The interface has no IDL parent, so it cannot reuse :class:`HTMLCollection`
    and carries the full named/indexed interceptor set the Chromium baseline
    declares for it.
    """
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("item", "fn_item", 0, 1), ("namedItem", "fn_namedItem", 1, 1))
    def __init__(self, owner=None, getter=None, **_):
        self._owner, self._getter = owner, getter

    def _items(self):
        items = self._getter() if self._getter else ()
        return items if isinstance(items, list) else list(items)

    def _matches(self, name):
        """Elements whose ``id`` or ``name`` equals ``name``, in tree order."""
        if not name:
            return []
        return [n for n in self._items() if _node_id(n) == name or _node_name(n) == name]

    def _named(self, name):
        # Chromium returns the single match, a live HTMLCollection when several
        # elements share the id or name, and null when nothing matches.
        matches = self._matches(name)
        if not matches:
            return pyv8.Null
        if len(matches) == 1:
            return matches[0]
        return HTMLCollection(owner=self._owner, getter=lambda: self._matches(name))

    def get_length(self): return len(self._items())

    def fn_item(self, *args):
        # The IDL argument is a DOMString; canonical array-index strings use
        # indexed lookup, while every other string uses the named lookup.
        if not args or args[0] is pyv8.Undefined:
            return pyv8.Null
        name = _dom_string(args[0])
        index = _array_index_name(name)
        if index is None:
            return self._named(name)
        items = self._items()
        return items[index] if 0 <= index < len(items) else pyv8.Null

    def fn_namedItem(self, *args):
        if not args:
            raise V8TypeError(
                "Failed to execute 'namedItem' on 'HTMLAllCollection': 1 argument required, but only 0 present."
            )
        return self._named(_dom_string(args[0]))

    def __call__(self, *args):
        if not args or args[0] is pyv8.Undefined:
            return pyv8.Null
        name = _dom_string(args[0])
        index = _array_index_name(name)
        if index is not None:
            items = self._items()
            return items[index] if index < len(items) else pyv8.Null
        return self._named(name)

    # -- named interceptor ----------------------------------------------
    def __v8_name_get__(self, name):
        if not isinstance(name, str):
            return None
        value = self._named(name)
        return None if value is pyv8.Null else value

    def __v8_name_set__(self, name, value):
        if self._matches(_dom_string(name)):
            raise LegacyPropertyError("Named property setter is not supported.")
        return None
    def __v8_name_query__(self, name):
        if not isinstance(name, str):
            return None
        return _ALL_NAME_ATTRIBUTES if self._matches(name) else None
    def __v8_name_del__(self, name):
        if self._matches(_dom_string(name)):
            raise LegacyPropertyError("Named property deleter is not supported.")
        return None
    def __v8_name_enum__(self):
        # OwnPropertyKeys includes legacy names; LegacyUnenumerable controls
        # their enumerable bit, not whether they are returned here.
        names = []
        seen = set()
        for node in self._items():
            for value in (_node_id(node), _node_name(node)):
                if value and value not in seen:
                    seen.add(value); names.append(value)
        return names

    # -- indexed interceptor --------------------------------------------
    def __v8_index_get__(self, index):
        value = self.fn_item(index)
        return None if value is pyv8.Null else value
    def __v8_index_set__(self, index, value):
        raise LegacyPropertyError("Indexed property setter is not supported.")
    def __v8_index_query__(self, index):
        try:
            return V8Attribute.ReadOnly if 0 <= int(index) < len(self._items()) else None
        except (TypeError, ValueError):
            return None
    def __v8_index_del__(self, index):
        try:
            supported = 0 <= int(index) < len(self._items())
        except (TypeError, ValueError):
            supported = False
        if supported:
            raise LegacyPropertyError("Index property deleter is not supported.")
        return True
    def __v8_index_enum__(self): return len(self._items())

    def __iter__(self): return iter(self._items())
    def __len__(self): return len(self._items())


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
