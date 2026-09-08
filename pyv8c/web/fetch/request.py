"""Fetch request model."""
from pyv8 import IdlSpec, AttributeSpec

from urllib.parse import urljoin




from ...engine.registry import interface
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

    def __init__(self, url="", options=None, services=None, _realm=None, **_):
        options = options if isinstance(options, dict) else {}
        self._services = services or _realm
        base = self._services.realm_services.url if self._services else "about:blank"
        self._url = urljoin(base, str(url))
        self._method = str(options.get("method", "GET")).upper()
        self._headers = Headers(options.get("headers"))
        self._body = options.get("body")
        self._mode = str(options.get("mode", "cors"))
        self._credentials = str(options.get("credentials", "same-origin"))
        self._redirect = str(options.get("redirect", "follow"))
        self._timeout = options.get("timeout")

    def get_url(self): return self._url
    def get_method(self): return self._method
    def get_headers(self): return self._headers
    def get_mode(self): return self._mode
    def get_credentials(self): return self._credentials


__all__ = ["Request"]
