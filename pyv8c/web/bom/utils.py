"""Helpers shared by the browser object model modules; more than one module
needs each of them.
"""

from urllib.parse import urlsplit

from pyv8 import AttributeSpec, MethodSpec
from pyv8.flag import FlagCrossOriginCheck, FlagLocation, SideEffectType

from ...foundation.errors import SecurityError


def deny_cross_origin():
    raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")


def document_key(url):
    """The URL parts a same-document (fragment) navigation cannot change."""
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc, parts.path, parts.query)


def is_secure_context(url):
    """Whether *url* is an origin a browser treats as potentially trustworthy."""
    text = str(url)
    # A blob (or filesystem) URL is as trustworthy as the origin it carries.
    if text[:5].lower() == "blob:":
        return is_secure_context(text[5:])
    parts = urlsplit(text)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme in ("https", "wss", "file", "data"):
        return True
    if scheme in ("http", "ws"):
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
    return False


def window_attribute(name, getter, setter=None, *, cross_origin=False, flags=0, side_effect=SideEffectType.kHasSideEffect):
    check = FlagCrossOriginCheck.kCheck if cross_origin else FlagCrossOriginCheck.kDoNotCheck
    return AttributeSpec(name=name, getter=getter, setter=setter, location=FlagLocation.kInstance, property_flags=flags, cross_origin_get_check=check, cross_origin_set_check=check, side_effect=side_effect)


def window_method(name, callback, length=0, *, cross_origin=False, side_effect=SideEffectType.kHasSideEffect):
    check = FlagCrossOriginCheck.kCheck if cross_origin else FlagCrossOriginCheck.kDoNotCheck
    return MethodSpec(name=name, callback=callback, length=length, location=FlagLocation.kInstance, cross_origin_check=check, side_effect=side_effect)
