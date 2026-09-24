"""URL and origin primitives shared by every layer.

Origin computation and origin serialization are pure URL facts: they need
neither a DOM, nor a network transport, nor a page.  Keeping them here gives
``network``, ``web/bom`` and ``platform`` one implementation of the
special-scheme list and the default-port table, and lets platform code ask for
an origin without importing a transport module.
"""

from urllib.parse import urljoin, urlsplit

__all__ = [
    "DEFAULT_PORTS", "SPECIAL_SCHEMES",
    "origin_tuple", "same_origin", "serialize_origin", "origin_string",
    "absolute_url",
]

#: Schemes whose origin is derived from host and port ("special schemes" in
#: the URL standard).  Every other URL has an opaque origin.
SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})

#: Port implied by a special scheme when the URL omits it.
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}


def origin_tuple(value, *, inherited=None):
    """Return the browser origin triple for a URL.

    A tuple origin is ``(scheme, host, port)``.  Opaque origins are
    ``("opaque", sentinel)``: the sentinel is a fresh object so two unrelated
    opaque origins never compare equal, which is exactly how browsers treat
    them.  ``inherited`` supplies the origin that ``about:blank`` and
    unresolvable ``blob:`` URLs borrow from their creator.
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

    ``urljoin`` drops a bare ``?``, and an empty query is still a query: a GET
    form with no successful controls lands on ``/echo?`` in Chrome, and the
    request line says so.  A URL that already carries a scheme and a host is
    therefore sent exactly as the page wrote it.
    """
    parts = urlsplit(str(url))
    if parts.scheme and parts.netloc:
        return str(url)
    return urljoin(str(base), str(url))
