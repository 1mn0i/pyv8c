"""Shared DOM helpers and the metadata every legacy Web IDL collection uses."""

import re

from pyv8.flag import FlagArrayProto, V8Attribute

from ...foundation.arguments import dom_string

#: XML-compatible qualified name, shared by ``Document.createElement`` and the
#: attribute-creating APIs.
_VALID_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")

#: The only namespace this DOM implementation models (HTML documents).
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"

#: Iterator bits published through ``IdlSpec(array=...)`` by iterable collections.
ITERABLE_FLAGS = (
    FlagArrayProto.iterator | FlagArrayProto.entries | FlagArrayProto.keys
    | FlagArrayProto.values | FlagArrayProto.forEach
)

#: ``Object.prototype`` string keys (plus ``__proto__``).  Legacy named
#: properties neither shadow nor enumerate these names.
OBJECT_PROTOTYPE_NAMES = frozenset((
    "constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty",
    "__lookupGetter__", "__lookupSetter__", "isPrototypeOf",
    "propertyIsEnumerable", "toString", "valueOf", "__proto__",
    "toLocaleString",
))

#: Descriptor flags shared by every legacy named property.
NAMED_PROPERTY_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum


def dash_name(name):
    """``fooBar`` -> ``foo-bar``: the dashed spelling a camelCase name reflects.

    One rule serves both a CSS property name and the ``data-*`` attribute behind
    ``DOMStringMap``, so it lives here rather than in either reader.  A name that
    already starts with ``--`` is its own dashed spelling.
    """
    if name.startswith("--"):
        return name
    return re.sub(r"([A-Z])", lambda match: "-" + match.group(1).lower(), name)


def camel_name(name):
    """``foo-bar`` -> ``fooBar``; ``""`` when a dash ends the name.

    The empty string is what a caller checks for: a trailing dash cannot be spelled
    back, so the name is not one a reader may expose.
    """
    if name.startswith("--"):
        return name
    result = name
    while "-" in result:
        left, right = result.split("-", 1)
        if not right:
            return ""
        result = left + right[0].upper() + right[1:]
    return result


def as_nodes(values, parent):
    """Convert a Web IDL ``(Node or DOMString)...`` argument list to nodes."""
    # Imported here rather than at module scope: the node model imports this
    # module, so a module-level import would close a cycle.
    from .character_data import Text
    from .nodes import Node

    services = getattr(parent, "_services", None)
    return [
        value if isinstance(value, Node) else Text(dom_string(value), services=services)
        for value in values
    ]


_element_type = None


def is_element(node):
    """Whether ``node`` is a DOM ``Element``.

    ``elements`` sits above the node model, so the class is resolved on first
    use and cached afterwards: callers run this while walking the tree.
    """
    global _element_type
    if _element_type is None:
        from .html_elements import Element

        _element_type = Element
    return isinstance(node, _element_type)
