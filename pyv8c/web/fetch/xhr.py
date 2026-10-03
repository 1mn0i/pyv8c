"""XMLHttpRequest implementation."""
import re
from urllib.parse import urljoin, urlsplit

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, byte_string, dom_string, integer, missing_error, usv_string
from ...foundation.errors import InvalidStateError, NotSupportedError, SecurityError, V8SyntaxError
from ..dom.events import Event, EventTarget
from .events import ErrorEvent
from .headers import Headers


@interface('XMLHttpRequestEventTarget', 'EventTarget')
class XMLHttpRequestEventTarget(EventTarget):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=0, has_constructor=1)
    pass


@interface('XMLHttpRequest', 'XMLHttpRequestEventTarget')
class XMLHttpRequest(XMLHttpRequestEventTarget):
    UNSENT, OPENED, HEADERS_RECEIVED, LOADING, DONE = 0, 1, 2, 3, 4
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="status", getter="get_status", side_effect=1),
            AttributeSpec(name="statusText", getter="get_statusText", side_effect=1),
            AttributeSpec(name="responseURL", getter="get_responseURL", side_effect=1),
            AttributeSpec(name="responseText", getter="get_responseText", side_effect=1),
            AttributeSpec(name="response", getter="get_response", side_effect=1),
            AttributeSpec(name="withCredentials", getter="get_withCredentials",
                          setter="set_withCredentials"),
            AttributeSpec(name="timeout", getter="get_timeout", setter="set_timeout"),
        ),
        methods=(
            MethodSpec(name="open", callback="fn_open", length=2),
            MethodSpec(name="send", callback="fn_send"),
            MethodSpec(name="setRequestHeader", callback="fn_setRequestHeader", length=2),
            MethodSpec(name="getResponseHeader", callback="fn_getResponseHeader", length=1, side_effect=1),
            MethodSpec(name="getAllResponseHeaders", callback="fn_getAllResponseHeaders"),
            MethodSpec(name="abort", callback="fn_abort"),
        ),
        constructor_behavior=1,
        has_constructor=1,
    )

    def __init__(self, *_extra, services=None, _realm=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)
        self._method = "GET"
        self._url = ""
        self._async = True
        self._request_headers = Headers()
        self._response = None
        self.readyState = self.UNSENT
        self.status = 0
        self.status_text = ""
        self.response_url = ""
        self.responseText = ""
        self.response = None
        self.timeout = 0
        self.withCredentials = False
        #: The load in flight and the deadline armed for it, so a timeout can drop it.
        self._handle = None
        self._timer = None

    def get_readyState(self): return self.readyState
    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_responseURL(self): return self.response_url
    def get_responseText(self): return self.responseText
    def get_response(self): return self.response
    def get_withCredentials(self): return bool(self.withCredentials)
    def set_withCredentials(self, value): self.withCredentials = boolean(value)
    def get_timeout(self): return self.timeout
    def set_timeout(self, value):
        """``unsigned long``, and ignored once the request is running."""
        if self.readyState not in (self.UNSENT, self.OPENED):
            return
        self.timeout = integer(value, bits=32, signed=False)

    def _state(self, value):
        self.readyState = value
        self.fn_dispatchEvent(Event("readystatechange"))

    def fn_open(self, method=MISSING, url=MISSING, async_value=True, *args):
        if method is MISSING:
            raise missing_error('XMLHttpRequest', 'open', 2, 0, constructor=False)
        if url is MISSING:
            raise missing_error('XMLHttpRequest', 'open', 2, 1, constructor=False)
        method = byte_string(method)
        url = usv_string(url)
        if async_value is pyv8.Undefined:
            async_value = True
        async_value = boolean(async_value)
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method):
            raise V8SyntaxError("Invalid HTTP method")
        if method.upper() in ("CONNECT", "TRACE", "TRACK"):
            raise SecurityError("Forbidden HTTP method")
        base = self._services.realm_services.url if self._services else "about:blank"
        resolved = urljoin(base, url)
        try:
            parts = urlsplit(resolved)
            if not parts.scheme:
                raise ValueError("missing scheme")
            # Reading the port is what rejects a URL whose port cannot be one.
            _ = parts.port
        except ValueError as exc:
            raise V8SyntaxError("Invalid URL") from exc
        if not async_value:
            raise NotSupportedError("synchronous XMLHttpRequest is not supported")
        self._method, self._url, self._async = method.upper(), resolved, async_value
        self._state(self.OPENED)

    def fn_setRequestHeader(self, key=MISSING, value=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('XMLHttpRequest', 'setRequestHeader', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('XMLHttpRequest', 'setRequestHeader', 2, 1, constructor=False)
        key = byte_string(key)
        value = byte_string(value)
        if self.readyState != self.OPENED:
            raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        self._request_headers.fn_set(key, value)

    def fn_getResponseHeader(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('XMLHttpRequest', 'getResponseHeader', 1, 0, constructor=False)
        name = dom_string(name)
        if not self._response:
            return pyv8.Null
        return self._response.headers.fn_get(name)

    def fn_send(self, body=None, *_extra):
        if body is pyv8.Undefined:
            body = None
        if self.readyState != self.OPENED:
            raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        request = {
            "method": self._method, "headers": self._request_headers, "body": body,
            "timeout": self.timeout / 1000 if self.timeout else None,
            "resource_type": "xhr",
        }
        if self._services:
            self._handle = self._services.resource_services.load_resource_async(
                self._url, request, self._finish)
            if self.timeout:
                self._timer = self._services.task_services.schedule(
                    self._on_timeout, self.timeout / 1000.0, repeat=False)

    def _finish(self, response, error):
        self._handle, self._timer = None, self._stop_timer()
        if not self._services or self._services.realm_services.closed:
            return
        if error:
            self.status = 0
            self._state(self.DONE)
            self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error)}))
            self.fn_dispatchEvent(Event("loadend"))
            return
        self._response = response
        self.status = response.status
        self.status_text = response.status_text
        # What the request ended at: a redirect the engine followed moves it.
        self.response_url = str(response.url or self._url)
        self.responseText = response._body_value()
        self.response = self.responseText
        self._state(self.HEADERS_RECEIVED)
        self._state(self.LOADING)
        self._state(self.DONE)
        self.fn_dispatchEvent(Event("load"))
        self.fn_dispatchEvent(Event("loadend"))

    def fn_getAllResponseHeaders(self, *_extra):
        """The response's headers as one block, lower-case and sorted as Chrome writes
        it.  ``Set-Cookie`` is not in it: a page may not read that header.
        """
        if not self._response:
            return ""
        lines = []
        for name, value in self._response.headers.fn_entries():
            if name == "set-cookie":
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value)
            lines.append("%s: %s\r\n" % (name, value))
        return "".join(lines)

    def fn_abort(self, *_extra):
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.cancel()
        self._timer = self._stop_timer()
        self._response = None
        self.readyState = self.UNSENT
        self.fn_dispatchEvent(Event("abort"))
        self.fn_dispatchEvent(Event("loadend"))

    def _stop_timer(self):
        """Disarm the timeout, answering with the ident so the caller can store it."""
        timer, self._timer = self._timer, None
        if timer is not None:
            self._services.task_services.cancel(timer)
        return None

    def _on_timeout(self):
        """A timeout drops the request and reports ``loadend`` alone -- no ``error``
        and no ``abort``, which is what Chrome sends.  The realm's task queue runs
        this, so a host loader that blocks the owner thread delays it.
        """
        self._timer = None
        handle, self._handle = self._handle, None
        if handle is None:
            return
        handle.cancel()
        self._response = None
        self.status = 0
        self._state(self.DONE)
        self.fn_dispatchEvent(Event("loadend"))


__all__ = ["XMLHttpRequestEventTarget", "XMLHttpRequest"]
