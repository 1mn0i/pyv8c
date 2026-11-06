"""Fetch request model."""
import re
from urllib.parse import urljoin, urlsplit

from pyv8c import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, abort_signal, byte_string, dictionary, enum_value, is_nullish, missing_error, usv_string
from ...foundation.arguments import IdlArgumentError
from ...foundation.urls import SPECIAL_SCHEMES
from ..page_errors import PageTypeError
from ..bom.abort import AbortSignal
from ..bom.url import URLSearchParams
from .formdata import FormData
from .headers import Headers
from ..fileapi.blob import Blob, _array_buffer_bytes


# Web IDL dictionary members are read in lexicographic order, including members
# this implementation does not otherwise use.  Their getters are observable.
_REQUEST_INIT_MEMBERS = (
    'adAuctionHeaders', 'body', 'cache', 'credentials', 'duplex', 'headers',
    'integrity', 'keepalive', 'method', 'mode', 'priority', 'privateToken',
    'redirect', 'referrer', 'referrerPolicy', 'signal', 'targetAddressSpace',
)
_REQUEST_ENUMS = {
    'credentials': ('omit', 'same-origin', 'include'),
    'mode': ('navigate', 'same-origin', 'no-cors', 'cors'),
    'redirect': ('follow', 'error', 'manual'),
}


def _request_init(options):
    source = dictionary(options, name='RequestInit', prefix="Failed to construct 'Request': ")
    values = {}
    for name in _REQUEST_INIT_MEMBERS:
        value = source.get(name, pyv8.Undefined)
        if value is pyv8.Undefined:
            continue
        if name in _REQUEST_ENUMS:
            value = enum_value(value, _REQUEST_ENUMS[name])
        elif name == 'method':
            value = byte_string(value)
        elif name == 'headers':
            value = Headers(value)
        values[name] = value
    return values


@interface('Request', None)
class Request:
    def __init__(self, url=MISSING, options=None, *_extra, services=None, _realm=None, **_):
        if url is MISSING:
            raise missing_error('Request', 'constructor', 1, 0, constructor=True)
        original = url if isinstance(url, Request) else None
        if original is None:
            url = usv_string(url)
        options = _request_init(options)
        self._services = services or _realm
        base = self._services.realm_services.url if self._services else "about:blank"
        self._url = original._url if original else urljoin(base, url)
        self._body = options.get("body", original._body if original else None)
        self._credentials = enum_value(options.get("credentials", original._credentials if original else "same-origin"), ("omit", "same-origin", "include"))
        self._headers = Headers(options.get("headers", original._headers if original else None))
        self._method = byte_string(options.get("method", original._method if original else "GET"))
        self._mode = enum_value(options.get("mode", original._mode if original else "cors"), ("navigate", "same-origin", "no-cors", "cors"))
        self._redirect = enum_value(options.get("redirect", original._redirect if original else "follow"), ("follow", "error", "manual"))
        self._timeout = None
        self._signal = self._read_signal(options, original)
        try:
            parts = urlsplit(self._url)
            valid_url = (bool(parts.scheme)
                         and (parts.scheme not in SPECIAL_SCHEMES or bool(parts.hostname))
                         and not parts.username and not parts.password)
            # Reading the port is what rejects a URL whose port cannot be one.
            _ = parts.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise PageTypeError(f"Failed to construct 'Request': Failed to parse URL from {url}")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self._method):
            raise PageTypeError("Failed to construct 'Request': Invalid HTTP method")
        upper = self._method.upper()
        if upper in ("CONNECT", "TRACE", "TRACK"):
            raise PageTypeError("Failed to construct 'Request': Unsupported HTTP method")
        if upper in ("DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT"):
            self._method = upper
        if self._mode == "navigate":
            raise PageTypeError("Failed to construct 'Request': Cannot construct a Request with a RequestInit whose mode member is set as 'navigate'.")
        if self._mode == "no-cors" and upper not in ("GET", "HEAD", "POST"):
            raise PageTypeError("Failed to construct 'Request': Unsupported method in no-cors mode")
        if upper in ("GET", "HEAD") and not is_nullish(self._body):
            raise PageTypeError("Failed to construct 'Request': Request with GET/HEAD method cannot have body.")
        binary = _array_buffer_bytes(self._body)
        if binary is not None:
            self._body = binary
        self._type_the_body()

    def _type_the_body(self):
        """Give the body's own type to ``Content-Type`` when the author named none.

        This is Fetch's "extract a body": a string travels as
        ``text/plain;charset=UTF-8``, ``URLSearchParams`` as urlencoded text, and a
        ``Blob`` with the type it was built with.  Chrome sends the string type on
        the Cloudflare challenge's submit POST -- its answer carries the widget's
        result -- and a submit arriving without it is answered ``400`` instead, which
        is where the managed challenge stopped one step before ``cf_clearance``
        (measured against Chrome 150/155, 2026-11-03).  ``XMLHttpRequest`` has the
        same rule in ``xhr._default_content_type`` because ``send`` applies it there.
        """
        if is_nullish(self._body):
            return
        #: ``fn_get`` answers ``Null`` for a header it does not hold, not ``None``.
        if not is_nullish(self._headers.fn_get("content-type")):
            return
        body = self._body
        if isinstance(body, Blob):
            declared = body.get_type()
            if declared:
                self._headers.fn_set("Content-Type", declared)
            return
        if isinstance(body, (bytes, bytearray, memoryview)):
            return
        if isinstance(body, URLSearchParams):
            self._headers.fn_set("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
            return
        if isinstance(body, FormData):
            #: The multipart boundary is chosen when the body is serialized.
            return
        self._headers.fn_set("Content-Type", "text/plain;charset=UTF-8")

    def get_url(self): return self._url
    def _read_signal(self, options, original):
        """The signal this request cancels through.

        A request always has a signal, and it is always its own: naming one in the
        init makes the request's signal follow it rather than be it, so the two are
        never the same object and the reason travels across.
        """
        try:
            given = abort_signal(options.get(
                "signal", original._signal if original else None))
        except (PageTypeError, IdlArgumentError) as exc:
            raise PageTypeError("Failed to construct 'Request': Failed to read the 'signal'"
                              " property from 'RequestInit': Failed to convert value to"
                              " 'AbortSignal'.") from exc
        signal = AbortSignal(services=self._services)
        if given is not None:
            given._subscribe(lambda: signal._abort(given._reason))
        return signal
    def get_method(self): return self._method
    def get_headers(self): return self._headers
    def get_mode(self): return self._mode
    def get_credentials(self): return self._credentials
    def get_destination(self):
        """A request a script built was not created by the browser for a target."""
        return ""
    def get_bodyUsed(self):
        # Nothing here consumes the body by streaming it; it is read in Python.
        return False
    def get_signal(self): return self._signal

__all__ = ["Request"]
