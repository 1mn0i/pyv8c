"""DOM collection views: live node lists and token lists.

These classes deliberately have no dependency on the node model itself:
they read through owner-supplied getters, which keeps them importable by
:mod:`dom.nodes` without a cycle.
"""
import v8n
from v8n import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string, integer, interface_value, missing_error
from ...foundation.errors import (
    InvalidCharacterError,
    V8TypeError,
    dom_exception,
)
from .mixins import LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin
from .utils import ITERABLE_FLAGS


@interface('NodeList', None)
class NodeList(ReadOnlyIndexedPropertyMixin):
    """A live list view. The owner remains the source of truth."""
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),),
        constructor_behavior=0,
        to_string_tag="NodeList",
        has_constructor=1,
        array=ITERABLE_FLAGS,
    )
    def __init__(self, owner=None, getter=None, values=None, **_):
        self._owner, self._getter, self._values = owner, getter, values
    def _items(self):
        items = self._getter() if self._getter else (self._values or ())
        return items if hasattr(items, "__len__") and hasattr(items, "__getitem__") else tuple(items)
    def _indexed_items(self): return self._items()
    def get_length(self): return len(self._items())
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('NodeList', 'item', 1, 0, constructor=False)
        index = integer(index)
        return self._item_at(index)
    def __iter__(self): return iter(self._items())
    def __getitem__(self, index): return self._items()[index]
    def __len__(self): return len(self._items())


@interface('NamedNodeMap', None)
class NamedNodeMap(NodeList):
    """Live attribute collection owned by an Element."""
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="getNamedItem", callback="fn_getNamedItem", length=1, side_effect=1),
            MethodSpec(name="setNamedItem", callback="fn_setNamedItem", length=1),
            MethodSpec(name="removeNamedItem", callback="fn_removeNamedItem", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="NamedNodeMap",
        has_constructor=1,
    )
    def __init__(self, element=None, **_):
        self._element = element
        super().__init__(getter=self._attribute_items)
    def _attribute_items(self):
        from .nodes import Attr
        return [Attr(k, v, ownerElement=self._element, services=self._element._services)
                for k, v in self._element.attributes.items()]
    def fn_getNamedItem(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('NamedNodeMap', 'getNamedItem', 1, 0, constructor=False)
        name = dom_string(name)
        return next((item for item in self._items() if item.get_name() == str(name)), v8n.Null)
    def fn_setNamedItem(self, attr=MISSING, *_extra):
        if attr is MISSING:
            raise missing_error('NamedNodeMap', 'setNamedItem', 1, 0, constructor=False)
        attr = interface_value(attr, 'Attr', 'NamedNodeMap', 'setNamedItem', 1, nullable=False)
        from .nodes import Attr
        if not isinstance(attr, Attr): raise V8TypeError("parameter is not of type Attr")
        old = self.fn_getNamedItem(attr.get_name())
        self._element.attributes[attr.get_name()] = attr.get_value()
        return old
    def fn_removeNamedItem(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('NamedNodeMap', 'removeNamedItem', 1, 0, constructor=False)
        name = dom_string(name)
        from ...foundation.errors import NotFoundError
        name = str(name)
        if name not in self._element.attributes: raise NotFoundError("The attribute was not found.")
        from .nodes import Attr
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


@interface('HTMLCollection', None)
class HTMLCollection(LegacyNamedPropertyMixin, NodeList):
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="namedItem", callback="fn_namedItem", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="HTMLCollection",
        has_constructor=1,
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
    )
    def fn_namedItem(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('HTMLCollection', 'namedItem', 1, 0, constructor=False)
        name = dom_string(name)
        value = str(name)
        if not value:
            return v8n.Null
        return next((n for n in self._items()
                     if _node_id(n) == value or _collection_node_name(n) == value), v8n.Null)

    def _supported_names(self):
        return _supported_names(self._items(), _collection_node_name)

    def _named_item(self, name):
        return self.fn_namedItem(name)


def _array_index_name(value):
    """Return a canonical array index, or ``None`` for a normal property name."""
    if not value or any(ch < "0" or ch > "9" for ch in value) or (len(value) > 1 and value[0] == "0"):
        return None
    try:
        index = int(value)
    except ValueError:
        return None
    return index if index < 2 ** 32 - 1 else None


# ``HTMLAllCollection`` is declared ``[LegacyUnenumerableNamedProperties]``:
# the named properties exist and are reachable, but their descriptors are
# non-enumerable (so they are omitted by ``Object.keys`` and ``for-in``).
@interface('HTMLAllCollection', None)
class HTMLAllCollection(LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin):
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
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="item", callback="fn_item", side_effect=1),
            MethodSpec(name="namedItem", callback="fn_namedItem", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="HTMLAllCollection",
        has_constructor=1,
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
    )
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
            return v8n.Null
        if len(matches) == 1:
            return matches[0]
        return HTMLCollection(owner=self._owner, getter=lambda: self._matches(name))

    def get_length(self): return len(self._items())

    def fn_item(self, *args):
        if not args or args[0] is v8n.Undefined:
            return v8n.Null
        name = dom_string(args[0])
        index = _array_index_name(name)
        if index is None:
            return self._named(name)
        items = self._items()
        return items[index] if 0 <= index < len(items) else v8n.Null

    def fn_namedItem(self, *args):
        if not args:
            raise V8TypeError(
                "Failed to execute 'namedItem' on 'HTMLAllCollection': 1 argument required, but only 0 present."
            )
        return self._named(dom_string(args[0]))

    def __call__(self, *args):
        if not args or args[0] is v8n.Undefined:
            return v8n.Null
        name = dom_string(args[0])
        index = _array_index_name(name)
        if index is not None:
            items = self._items()
            return items[index] if index < len(items) else v8n.Null
        return self._named(name)

    # -- named interceptor ----------------------------------------------
    def _supported_names(self):
        return _supported_names(self._items(), _all_node_name)

    def _named_item(self, name):
        return self._named(name)

    # -- indexed interceptor --------------------------------------------
    def _indexed_items(self): return self._items()

    def __iter__(self): return iter(self._items())
    def __len__(self): return len(self._items())


@interface('DOMTokenList', None)
class DOMTokenList(ReadOnlyIndexedPropertyMixin):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="length", getter="get_length", side_effect=1),
            AttributeSpec(name="value", getter="get_value", setter="set_value"),
        ),
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="contains", callback="fn_contains", length=1, side_effect=1),
            MethodSpec(name="add", callback="fn_add"),
            MethodSpec(name="remove", callback="fn_remove"),
            MethodSpec(name="toggle", callback="fn_toggle", length=1),
            MethodSpec(name="replace", callback="fn_replace", length=2),
            MethodSpec(name="supports", callback="fn_supports", length=1),
            MethodSpec(name="toString", callback="fn_toString", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="DOMTokenList",
        has_constructor=1,
        array=ITERABLE_FLAGS,
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

    def _validate(self, token, method=None):
        token = str(token)
        prefix = f"Failed to execute '{method}' on 'DOMTokenList': " if method else ""
        if not token:
            raise dom_exception("SyntaxError", prefix + "The token provided must not be empty.")
        if any(ch in self._ASCII_WHITESPACE for ch in token):
            detail = "The token provided contains HTML space characters, which are not valid in tokens."
            if method in ("add", "remove", "toggle", "replace"):
                detail = f"The token provided ('{token}') contains HTML space characters, which are not valid in tokens."
            raise InvalidCharacterError(prefix + detail)
        return token

    def _update(self, values):
        values = list(dict.fromkeys(values))
        if values or self._attr in self._element.attributes:
            self._element.attributes[self._attr] = " ".join(values)

    def get_length(self): return len(self._tokens())
    def get_value(self): return self._raw_value()
    def set_value(self, value):
        value = dom_string(value)
        self._element.attributes[self._attr] = str(value)
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('DOMTokenList', 'item', 1, 0, constructor=False)
        index = integer(index)
        return self._item_at(index)
    def fn_contains(self, token=MISSING, *_extra):
        if token is MISSING:
            raise missing_error('DOMTokenList', 'contains', 1, 0, constructor=False)
        token = dom_string(token)
        return token in self._tokens()

    def fn_add(self, *tokens):
        tokens = [dom_string(token) for token in tokens]
        tokens = [self._validate(token, "add") for token in tokens]
        self._update(self._tokens() + tokens)

    def fn_remove(self, *tokens):
        tokens = [dom_string(token) for token in tokens]
        tokens = set(self._validate(token, "remove") for token in tokens)
        self._update(token for token in self._tokens() if token not in tokens)

    def fn_toggle(self, token=MISSING, force=v8n.Undefined, *_extra):
        if token is MISSING:
            raise missing_error('DOMTokenList', 'toggle', 1, 0, constructor=False)
        token = dom_string(token)
        if force is not v8n.Undefined:
            force = boolean(force)
        token = self._validate(token, "toggle")
        values = self._tokens()
        present = token in values
        if present:
            if force is v8n.Undefined or not force:
                self._update(value for value in values if value != token)
                return False
            return True
        if force is v8n.Undefined or force:
            self._update(values + [token])
            return True
        return False

    def fn_replace(self, old=MISSING, new=MISSING, *_extra):
        if old is MISSING:
            raise missing_error('DOMTokenList', 'replace', 2, 0, constructor=False)
        if new is MISSING:
            raise missing_error('DOMTokenList', 'replace', 2, 1, constructor=False)
        old = dom_string(old)
        new = dom_string(new)
        old, new = self._validate(old, "replace"), self._validate(new, "replace")
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

    def fn_supports(self, token=MISSING, *_extra):
        if token is MISSING:
            raise missing_error('DOMTokenList', 'supports', 1, 0, constructor=False)
        token = dom_string(token)
        if self._supported_tokens is None:
            raise V8TypeError("DOMTokenList has no supported tokens.")
        return str(token).lower() in self._supported_tokens

    def fn_toString(self, *_extra):
        return self.get_value()
    def __iter__(self): return iter(self._tokens())
