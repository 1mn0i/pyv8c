"""URL and origin primitives shared by every layer.

Origin computation is a pure URL fact, so the special-scheme list and default-port
table live here and any layer can ask for an origin without a transport.
"""

from urllib.parse import urljoin, urlsplit

__all__ = [
    "DEFAULT_PORTS", "SPECIAL_SCHEMES",
    "origin_tuple", "same_origin", "serialize_origin", "origin_string",
    "absolute_url", "quote_form", "form_urlencoded",
]

#: Schemes whose origin is derived from host and port ("special schemes"); the rest are opaque.
SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})

#: Port implied by a special scheme when the URL omits it.
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}


def origin_tuple(value, *, inherited=None):
    """Return the browser origin triple for a URL, ``(scheme, host, port)``.

    An opaque origin is ``("opaque", sentinel)`` with a fresh sentinel, so two never
    compare equal; ``inherited`` is what ``about:blank`` and unresolvable ``blob:``
    URLs borrow from their creator.
    """
    raw = str(value)
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme == "about" and parts.path.lower() in ("blank", "srcdoc") and inherited is not None:
        return tuple(inherited)
    if scheme == "blob":
        embedded_origin = origin_tuple(raw[5:], inherited=inherited)
        if embedded_origin[0] != "opaque":
            return embedded_origin
        if inherited is not None:
            return tuple(inherited)
    if scheme in SPECIAL_SCHEMES and parts.hostname:
        default = DEFAULT_PORTS.get(scheme)
        try:
            port = parts.port
        except ValueError:
            return "opaque", object()
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        return scheme, hostname, port if port is not None else default
    return "opaque", object()


def same_origin(left, right):
    """True when two URLs share an origin.  Opaque origins are never equal."""
    return origin_tuple(left) == origin_tuple(right)


def serialize_origin(origin):
    """Serialize an origin triple the way ``Window.origin`` and postMessage do."""
    if origin[0] == "opaque":
        return "null"
    scheme, host, port = origin
    host = "[" + host + "]" if ":" in host else host
    port_suffix = "" if port == DEFAULT_PORTS.get(scheme) else ":" + str(port)
    return f"{scheme}://{host}{port_suffix}"


def origin_string(url):
    """Serialize the origin of ``url``; ``"null"`` for an opaque origin."""
    try:
        parts = urlsplit(str(url))
        if parts.scheme not in SPECIAL_SCHEMES or not parts.hostname:
            return "null"
        host = parts.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        port = parts.port
        default_port = DEFAULT_PORTS.get(parts.scheme)
        suffix = (":" + str(port)) if port is not None and port != default_port else ""
        return f"{parts.scheme}://{host}{suffix}"
    except (TypeError, ValueError):
        return "null"


def absolute_url(url, base):
    """Resolve *url* against *base*, keeping it verbatim when it is already absolute.

    ``urljoin`` drops a bare ``?``, but a GET form with no successful controls lands on
    ``/echo?`` in Chrome, so an absolute URL is sent exactly as the page wrote it.
    """
    parts = urlsplit(str(url))
    if parts.scheme and parts.netloc:
        return str(url)
    return urljoin(str(base), str(url))


#: The characters the urlencoded serializers leave alone, per the URL standard.
_FORM_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*-._"
)


def quote_form(text):
    """One name or value in ``application/x-www-form-urlencoded`` spelling."""
    out = []
    for byte in str(text).encode("utf-8"):
        char = chr(byte)
        if char in _FORM_SAFE:
            out.append(char)
        elif char == " ":
            out.append("+")
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def form_urlencoded(entries):
    """The ``application/x-www-form-urlencoded`` spelling of *entries*."""
    return "&".join(f"{quote_form(name)}={quote_form(value)}" for name, value in entries)
