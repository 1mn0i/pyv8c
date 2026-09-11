"""Fetch request model."""
import re
from urllib.parse import urljoin, urlsplit

from pyv8 import AttributeSpec, IdlSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, byte_string, dictionary, enum_value, is_nullish, missing_error, usv_string
from ...foundation.errors import V8TypeError
from .headers import Headers


@interface('Request', None)
class Request:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="url", getter="get_url", side_effect=1),
            AttributeSpec(name="method", getter="get_method", side_effect=1),
            AttributeSpec(name="headers", getter="get_headers", side_effect=1),
            AttributeSpec(name="mode", getter="get_mode", side_effect=1),
            AttributeSpec(name="credentials", getter="get_credentials", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, url=MISSING, options=None, *_extra, services=None, _realm=None, **_):
        if url is MISSING:
            raise missing_error('Request', 'constructor', 1, 0, constructor=True)
        original = url if isinstance(url, Request) else None
        if original is None:
            url = usv_string(url)
        options = dictionary(options, name="RequestInit", prefix="Failed to construct 'Request': ")
        self._services = services or _realm
        base = self._services.realm_services.url if self._services else "about:blank"
        self._url = original._url if original else urljoin(base, url)
        self._body = options.get("body", original._body if original else None)
        self._credentials = enum_value(options.get("credentials", original._credentials if original else "same-origin"), ("omit", "same-origin", "include"))
        self._headers = Headers(options.get("headers", original._headers if original else None))
        self._method = byte_string(options.get("method", original._method if original else "GET"))
        self._mode = enum_value(options.get("mode", original._mode if original else "cors"), ("navigate", "same-origin", "no-cors", "cors"))
        self._redirect = enum_value(options.get("redirect", original._redirect if original else "follow"), ("follow", "error", "manual"))
        self._timeout = options.get("timeout")
        try:
            parts = urlsplit(self._url)
            valid_url = bool(parts.scheme) and not parts.username and not parts.password
            parts.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise V8TypeError("Failed to construct 'Request': Failed to parse URL")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self._method):
            raise V8TypeError("Failed to construct 'Request': Invalid HTTP method")
        upper = self._method.upper()
        if upper in ("CONNECT", "TRACE", "TRACK"):
            raise V8TypeError("Failed to construct 'Request': Unsupported HTTP method")
        if upper in ("DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT"):
            self._method = upper
        if self._mode == "navigate":
            raise V8TypeError("Failed to construct 'Request': Cannot construct a Request with a RequestInit whose mode member is set as 'navigate'.")
        if self._mode == "no-cors" and upper not in ("GET", "HEAD", "POST"):
            raise V8TypeError("Failed to construct 'Request': Unsupported method in no-cors mode")
        if upper in ("GET", "HEAD") and not is_nullish(self._body):
            raise V8TypeError("Failed to construct 'Request': Request with GET/HEAD method cannot have body.")

    def get_url(self): return self._url
    def get_method(self): return self._method
    def get_headers(self): return self._headers
    def get_mode(self): return self._mode
    def get_credentials(self): return self._credentials


__all__ = ["Request"]
