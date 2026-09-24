"""The proxy a context sends its traffic through, in the one shape engines read.

A proxy is configured once -- on the Browser, overridden per Context -- and
every engine reads the same value out of its own options, so the value is
normalized here before it reaches any of them:

* ``None``   -- nothing was configured, which the assembly turns into a direct
  connection rather than leaving it to the engine;
* ``""``     -- a direct connection was asked for explicitly;
* a URL      -- ``http://user:pass@host:port``, where credentials in the URL
  are what the proxy is authenticated with.

Only ``http`` and ``https`` proxies exist here: both engines reach the target
through ``CONNECT`` and send ``Proxy-Authorization`` from the URL.  SOCKS is not
implemented, and a ``socks5://`` value is refused by name instead of failing
somewhere less legible.

Nothing here reads a proxy from the environment.  Both engines would: libcurl
picks up ``HTTP_PROXY``/``ALL_PROXY`` on its own, and the websockets client's
``proxy`` defaults to true, meaning "whatever the environment says".  A context
that was not given a proxy is therefore told so explicitly (see
:func:`curl_proxies` and :func:`websockets_proxy`), which is what makes an
unconfigured browser go direct on a machine that has a proxy set.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: The option key that carries a proxy into an engine, in either role.
PROXY_OPTION = "proxy"

#: The keys an engine's own options may carry a proxy in.  One of these next to
#: ``proxy=`` is refused rather than resolved silently (see ``network.assembly``).
RAW_OPTION_KEYS = ("proxy", "proxies", "proxy_auth")

#: The proxy schemes that are supported.
SCHEMES = ("http", "https")


def normalize_proxy(value):
    """Validate and canonicalize a ``proxy=`` argument.

    :param value: ``None`` for "nothing configured", ``""`` for a direct
        connection, or an ``http``/``https`` proxy URL with the credentials, if
        any, in its userinfo.
    :returns: ``None``, ``""``, or the URL as written.
    :raises TypeError: *value* is neither a string nor ``None``.
    :raises ValueError: the value is not an ``http``/``https`` proxy URL --
        a missing scheme, a missing host, an unusable port, or a scheme this
        does not implement are each named in the message.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            "proxy must be a URL string, \"\" to connect directly, or None to "
            "inherit, not " + type(value).__name__
        )
    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        raise ValueError(f"proxy needs a scheme: {text!r} should look like http://host:port")
    parts = urlsplit(text)
    if parts.scheme.lower() not in SCHEMES:
        raise ValueError(
            f"unsupported proxy scheme {parts.scheme!r}: only http:// and https:// "
            "proxies are supported, and SOCKS is not implemented"
        )
    if not parts.hostname:
        raise ValueError(f"proxy needs a host: {text!r}")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError(f"proxy has an unusable port: {text!r} ({exc})") from exc
    # The scheme is the one part that is case-insensitive everywhere but spelled
    # by each client their own way, so it is written the way they both read it.
    scheme_end = text.index("://")
    return text[:scheme_end].lower() + text[scheme_end:]


def curl_proxies(value):
    """The ``proxies=`` argument for one curl_cffi request.

    ``None`` means "leave the request alone": the caller configured the engine's
    own proxy keys instead, which are its business.  Anything else becomes a
    mapping for both schemes -- including the empty string, which is what pins a
    request to a direct connection.  Measured against curl_cffi 0.16.3: an empty
    per-request value overrides ``HTTP_PROXY``/``ALL_PROXY`` from the
    environment, while ``proxy=""``, ``proxies={"http": None}`` and
    ``proxies=""`` all still go through the environment's proxy.
    """
    if value is None:
        return None
    return {"http": value, "https": value}


def websockets_proxy(value):
    """The ``proxy=`` argument for one websockets connection.

    The client's own default is ``True``, which means "read the environment", so
    a direct connection is asked for with ``None`` rather than by saying nothing.
    A value that is not a string is passed through untouched: ``True`` there is
    the client's own way of asking for the environment's proxy, which only an
    explicit ``ws_options`` can do.
    """
    if value is None or value == "":
        return None
    return value


__all__ = [
    "PROXY_OPTION",
    "RAW_OPTION_KEYS",
    "SCHEMES",
    "curl_proxies",
    "normalize_proxy",
    "websockets_proxy",
]
