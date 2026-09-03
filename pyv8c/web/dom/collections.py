"""DOM collection views: live node lists and token lists.

These classes deliberately have no dependency on the node model itself:
they read through owner-supplied getters, which keeps them importable by
:meth:`dom.model` without a cycle.
"""

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagArrayProto, FlagExposed, V8Attribute

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.errors import (
    V8TypeError,
    InvalidCharacterError,
    LegacyPropertyError,
    dom_exception,
)
from ...foundation.indexed_properties import ReadOnlyIndexedPropertyMixin


_ITERABLE_FLAGS = (
    FlagArrayProto.iterator | FlagArrayProto.entries | FlagArrayProto.keys
    | FlagArrayProto.values | FlagArrayProto.forEach
)


@interface("NodeList", None, constructible=False, tag="NodeList")
@ConstructorConfig(
    exposed=FlagExposed.kYes, constructor=0, has_constructor=1,
    v8_array=_ITERABLE_FLAGS,
)
class NodeList(ReadOnlyIndexedPropertyMixin):
    """A live list view. The owner remains the source of truth."""
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1))
    def __init__(self, owner=None, getter=None, values=None, **_):
        self._owner, self._getter, self._values = owner, getter, values
    def _items(self):
        items = self._getter() if self._getter else (self._values or ())
        return items if hasattr(items, "__len__") and hasattr(items, "__getitem__") else tuple(items)
    def _indexed_items(self): return self._items()
    def get_length(self): return len(self._items())
    def fn_item(self, index): return self._item_at(index)
    def __iter__(self): return iter(self._items())
    def __getitem__(self, index): return self._items()[index]
    def __len__(self): return len(self._items())


@interface("NamedNodeMap", None, constructible=False, tag="NamedNodeMap")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class NamedNodeMap(NodeList):
    """Live attribute collection owned by an Element."""
    __v8_method__ = js_methods(
        ("item", "fn_item", 1, 1), ("getNamedItem", "fn_getNamedItem", 1, 1),
        ("setNamedItem", "fn_setNamedItem", 1, 0), ("removeNamedItem", "fn_removeNamedItem", 1, 0),
    )
    def __init__(self, element=None, **_):
        self._element = element
        super().__init__(getter=self._attribute_items)
    def _attribute_items(self):
        from .model import Attr
        return [Attr(k, v, ownerElement=self._element, services=self._element._services)
                for k, v in self._element.attributes.items()]
    def fn_getNamedItem(self, name):
        return next((item for item in self._items() if item.get_name() == str(name)), pyv8.Null)
    def fn_setNamedItem(self, attr):
        from .model import Attr
        if not isinstance(attr, Attr): raise V8TypeError("parameter is not of type Attr")
        old = self.fn_getNamedItem(attr.get_name())
        self._element.attributes[attr.get_name()] = attr.get_value()
        return old
    def fn_removeNamedItem(self, name):
        from ...foundation.errors import NotFoundError
        name = str(name)
        if name not in self._element.attributes: raise NotFoundError("The attribute was not found.")
        from .model import Attr
        return Attr(name, self._element.attributes.pop(name), ownerElement=self._element, services=self._element._services)


def _node_id(node):
    """The ``id`` of any node-like object, without importing the node model."""
    getter = getattr(node, "get_id", None)
    value = getter() if callable(getter) else ""
    return str(value) if value else ""


def _all_node_name(node):
    tag = str(getattr(node, "localName", "")).lower()
    if tag not in {"a", "button", "embed", "form", "frame", "frameset",
                   "iframe", "img", "input", "map", "meta", "object",
                   "select", "textarea"}:
        return ""
    return str(getattr(node, "attributes", {}).get("name") or "")


def _collection_node_name(node):
    """The HTML ``name`` attribute used by HTMLCollection named lookup."""
    return str(getattr(node, "attributes", {}).get("name") or "")


def _supported_names(items, name_getter):
    """Return unique IDs and names in tree order, as required by Web IDL."""
    names = []
    seen = set()
    for node in items:
        for value in (_node_id(node), name_getter(node)):
            if value and value not in seen:
                seen.add(value)
                names.append(value)
    return names


# Legacy named properties do not override ordinary prototype properties.
# These are the string keys installed on the collection prototypes and on
# Object.prototype; symbol-based iterator members cannot collide with names.
_OBJECT_PROTOTYPE_NAMES = frozenset((
    "constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty",
    "__lookupGetter__", "__lookupSetter__", "isPrototypeOf",
    "propertyIsEnumerable", "toString", "valueOf", "__proto__",
    "toLocaleString",
))
_HTML_COLLECTION_PROTOTYPE_NAMES = _OBJECT_PROTOTYPE_NAMES | {
    "length", "item", "namedItem",
}


@interface("HTMLCollection", None, constructible=False, tag="HTMLCollection")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLCollection(NodeList):
    _NAME_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))
    def fn_namedItem(self, name):
        value = str(name)
        if not value:
            return pyv8.Null
        return next((n for n in self._items()
                     if _node_id(n) == value or _collection_node_name(n) == value), pyv8.Null)

    def _supported_names(self):
        return _supported_names(self._items(), _collection_node_name)

    def _visible_names(self):
        return [
            name for name in self._supported_names()
            if name not in _HTML_COLLECTION_PROTOTYPE_NAMES
        ]

    def __v8_name_get__(self, name):
        if not isinstance(name, str) or name not in self._visible_names():
            return None
        value = self.fn_namedItem(name)
        return None if value is pyv8.Null else value

    def __v8_name_set__(self, name, value):
        name = _dom_string(name)
        if name in self._visible_names():
            raise LegacyPropertyError.named(
                "set", name, self._interface_name(),
                "Named property setter is not supported.",
            )
        return None

    def __v8_name_query__(self, name):
        if not isinstance(name, str):
            return None
        return self._NAME_ATTRIBUTES if name in self._visible_names() else None

    def __v8_name_del__(self, name):
        name = _dom_string(name)
        if name in self._visible_names():
            raise LegacyPropertyError.named(
                "delete", name, self._interface_name(),
                "Named property deleter is not supported.",
            )
        return None

    def __v8_name_enum__(self):
        return self._visible_names()


# ``HTMLAllCollection`` is declared ``[LegacyUnenumerableNamedProperties]``:
# the named properties exist and are reachable, but their descriptors are
# non-enumerable (so they are omitted by ``Object.keys`` and ``for-in``).
_ALL_NAME_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum
_ALL_INTERFACE = "HTMLAllCollection"


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
class HTMLAllCollection(ReadOnlyIndexedPropertyMixin):
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
        return [n for n in self._items() if _node_id(n) == name or _all_node_name(n) == name]

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
        if not isinstance(name, str) or name in _HTML_COLLECTION_PROTOTYPE_NAMES:
            return None
        value = self._named(name)
        return None if value is pyv8.Null else value

    def __v8_name_set__(self, name, value):
        name = _dom_string(name)
        if name not in _HTML_COLLECTION_PROTOTYPE_NAMES and self._matches(name):
            raise LegacyPropertyError.named(
                "set", name, _ALL_INTERFACE,
                "Named property setter is not supported.",
            )
        return None
    def __v8_name_query__(self, name):
        if not isinstance(name, str):
            return None
        if name in _HTML_COLLECTION_PROTOTYPE_NAMES:
            return None
        return _ALL_NAME_ATTRIBUTES if self._matches(name) else None
    def __v8_name_del__(self, name):
        name = _dom_string(name)
        if name not in _HTML_COLLECTION_PROTOTYPE_NAMES and self._matches(name):
            raise LegacyPropertyError.named(
                "delete", name, _ALL_INTERFACE,
                "Named property deleter is not supported.",
            )
        return None
    def __v8_name_enum__(self):
        # OwnPropertyKeys includes legacy names; LegacyUnenumerable controls
        # their enumerable bit, not whether they are returned here.
        names = []
        seen = set()
        for node in self._items():
            for value in (_node_id(node), _all_node_name(node)):
                if (value and value not in seen
                        and value not in _HTML_COLLECTION_PROTOTYPE_NAMES):
                    seen.add(value); names.append(value)
        return names

    # -- indexed interceptor --------------------------------------------
    def _indexed_items(self): return self._items()

    def __iter__(self): return iter(self._items())
    def __len__(self): return len(self._items())


@interface("DOMTokenList", None, constructible=False, tag="DOMTokenList")
@ConstructorConfig(
    exposed=FlagExposed.kYes, constructor=0, has_constructor=1,
    v8_array=_ITERABLE_FLAGS,
)
class DOMTokenList(ReadOnlyIndexedPropertyMixin):
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1), ("value", "get_value", "set_value", 0, 0))
    __v8_method__ = js_methods(
        ("item", "fn_item", 1, 1), ("contains", "fn_contains", 1, 1),
        ("add", "fn_add", 0, 0), ("remove", "fn_remove", 0, 0),
        ("toggle", "fn_toggle", 1, 0), ("replace", "fn_replace", 2, 0),
        ("supports", "fn_supports", 1, 0), ("toString", "fn_toString", 0, 1),
    )
    _ASCII_WHITESPACE = " \t\n\f\r"

    def __init__(self, element=None, attr="class", supported_tokens=None, **_):
        self._element = element
        self._attr = attr
        self._supported_tokens = (
            None if supported_tokens is None
            else frozenset(str(token).lower() for token in supported_tokens)
        )

    def _raw_value(self):
        return str(self._element.attributes.get(self._attr, ""))

    def _tokens(self):
        value = self._raw_value()
        tokens = []
        current = []
        for character in value:
            if character in self._ASCII_WHITESPACE:
                if current:
                    token = "".join(current)
                    if token not in tokens:
                        tokens.append(token)
                    current = []
            else:
                current.append(character)
        if current:
            token = "".join(current)
            if token not in tokens:
                tokens.append(token)
        return tokens

    def _indexed_items(self): return self._tokens()

    def _validate(self, token):
        token = str(token)
        if not token:
            raise dom_exception("SyntaxError", "The token provided must not be empty.")
        if any(ch in self._ASCII_WHITESPACE for ch in token):
            raise InvalidCharacterError("The token provided contains HTML space characters, which are not valid in tokens.")
        return token

    def _update(self, values):
        values = list(dict.fromkeys(values))
        if values or self._attr in self._element.attributes:
            self._element.attributes[self._attr] = " ".join(values)

    def get_length(self): return len(self._tokens())
    def get_value(self): return self._raw_value()
    def set_value(self, value): self._element.attributes[self._attr] = str(value)
    def fn_item(self, index): return self._item_at(index)
    def fn_contains(self, token): return self._validate(token) in self._tokens()

    def fn_add(self, *tokens):
        tokens = [self._validate(token) for token in tokens]
        self._update(self._tokens() + tokens)

    def fn_remove(self, *tokens):
        tokens = set(self._validate(token) for token in tokens)
        self._update(token for token in self._tokens() if token not in tokens)

    def fn_toggle(self, token, force=pyv8.Undefined):
        token = self._validate(token)
        values = self._tokens()
        present = token in values
        if present:
            if force is pyv8.Undefined or not force:
                self._update(value for value in values if value != token)
                return False
            return True
        if force is pyv8.Undefined or force:
            self._update(values + [token])
            return True
        return False

    def fn_replace(self, old, new):
        old, new = self._validate(old), self._validate(new)
        values = self._tokens()
        if old not in values:
            return False
        if old != new:
            if new in values:
                values.remove(old)
            else:
                values[values.index(old)] = new
        self._update(values)
        return True

    def fn_supports(self, token):
        if self._supported_tokens is None:
            raise V8TypeError("DOMTokenList has no supported tokens.")
        return str(token).lower() in self._supported_tokens

    def fn_toString(self): return self.get_value()
    def __iter__(self): return iter(self._tokens())
