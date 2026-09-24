"""Shared DOM helpers and the metadata every legacy Web IDL collection uses."""

import re
from urllib.parse import urlsplit, urlunsplit

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


# -- form submission serialization --------------------------------------------
#: The three encodings ``<form enctype>`` selects between.
URLENCODED = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"
TEXT_PLAIN = "text/plain"

#: The characters the urlencoded serializer leaves alone, per the URL standard.
_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*-._"
)

#: Chromium's multipart boundary prefix, followed by sixteen random characters.
_BOUNDARY_PREFIX = "----WebKitFormBoundary"
_BOUNDARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def crlf(text):
    """*text* with every line ending normalized to CRLF."""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def replace_query(url, query):
    """*url* with its query replaced by *query*.

    The ``?`` stays even for an empty query: Chrome lands on ``/echo?``.
    """
    parts = urlsplit(str(url))
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{base}?{query}{fragment}"


def urlencoded(entries):
    """The ``application/x-www-form-urlencoded`` spelling of *entries*."""
    return "&".join(f"{_quote(name)}={_quote(value)}" for name, value in entries)


def encode_entries(entries, enctype):
    """The ``(body, Content-Type)`` *entries* are posted as; the body is UTF-8 bytes."""
    if enctype == MULTIPART:
        # Lazy: multipart is rare, and this has no place on the startup path.
        import random

        boundary = _BOUNDARY_PREFIX + "".join(random.choices(_BOUNDARY_ALPHABET, k=16))
        return _multipart(entries, boundary), f"{MULTIPART}; boundary={boundary}"
    if enctype == TEXT_PLAIN:
        return _text_plain(entries), TEXT_PLAIN
    return urlencoded(entries).encode("utf-8"), URLENCODED


def _quote(text):
    """One name or value in ``application/x-www-form-urlencoded`` spelling."""
    out = []
    for byte in str(text).encode("utf-8"):
        char = chr(byte)
        if char in _SAFE:
            out.append(char)
        elif char == " ":
            out.append("+")
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def _text_plain(entries):
    """Raw values, one CRLF-terminated line per entry."""
    return "".join(f"{name}={value}\r\n" for name, value in entries).encode("utf-8")


def _multipart(entries, boundary):
    """One part per entry, each with only a ``Content-Disposition`` header."""
    parts = []
    for name, value in entries:
        parts.append(f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="{_escape(name)}"\r\n'
                     f"\r\n{value}\r\n")
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8")


def _escape(name):
    """A part name with the characters that would break the header escaped."""
    return (str(name).replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22"))
