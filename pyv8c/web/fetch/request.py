"""Fetch request model."""

from urllib.parse import urljoin

from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes
from .headers import Headers


@interface("Request", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Request:
    __v8_attribute__ = js_attributes(
        ("url", "get_url", None, 0, 1), ("method", "get_method", None, 0, 1),
        ("headers", "get_headers", None, 0, 1), ("mode", "get_mode", None, 0, 1),
        ("credentials", "get_credentials", None, 0, 1),
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
