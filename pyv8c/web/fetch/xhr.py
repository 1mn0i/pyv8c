"""XMLHttpRequest implementation."""

import pyv8
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.errors import InvalidStateError, NotSupportedError
from ..dom.events import Event, EventTarget
from .events import ErrorEvent
from .headers import Headers


@interface("XMLHttpRequestEventTarget", "EventTarget")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class XMLHttpRequestEventTarget(EventTarget):
    pass


@interface("XMLHttpRequest", "XMLHttpRequestEventTarget", constructible=True)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class XMLHttpRequest(XMLHttpRequestEventTarget):
    UNSENT, OPENED, HEADERS_RECEIVED, LOADING, DONE = 0, 1, 2, 3, 4
    __v8_attribute__ = js_attributes(
        ("readyState", "get_readyState", None, 0, 1), ("status", "get_status", None, 0, 1),
        ("statusText", "get_statusText", None, 0, 1), ("responseText", "get_responseText", None, 0, 1),
        ("response", "get_response", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("open", "fn_open", 2, 0), ("send", "fn_send", 0, 0),
        ("setRequestHeader", "fn_setRequestHeader", 2, 0),
        ("getResponseHeader", "fn_getResponseHeader", 1, 1),
        ("abort", "fn_abort", 0, 0),
    )

    def __init__(self, services=None, _realm=None, **kw):
        super().__init__(services=services or _realm, **kw)
        self._method = "GET"
        self._url = ""
        self._async = True
        self._request_headers = Headers()
        self._response = None
        self.readyState = self.UNSENT
        self.status = 0
        self.status_text = ""
        self.responseText = ""
        self.response = None
        self.timeout = 0

    def get_readyState(self): return self.readyState
    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_responseText(self): return self.responseText
    def get_response(self): return self.response

    def _state(self, value):
        self.readyState = value
        self.fn_dispatchEvent(Event("readystatechange"))

    def fn_open(self, method, url, async_value=True, *args):
        self._method, self._url, self._async = str(method).upper(), str(url), bool(async_value)
        self._state(self.OPENED)
        if not self._async:
            raise NotSupportedError("synchronous XMLHttpRequest is not supported")

    def fn_setRequestHeader(self, key, value):
        if self.readyState != self.OPENED:
            raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        self._request_headers.fn_set(key, value)

    def fn_getResponseHeader(self, name):
        if not self._response:
            return pyv8.Null
        return self._response.headers.fn_get(name)

    def fn_send(self, body=None):
        if self.readyState != self.OPENED:
            raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        request = {
            "method": self._method, "headers": self._request_headers, "body": body,
            "timeout": self.timeout / 1000 if self.timeout else None,
        }
        if self._services:
            self._services.resource_services.load_resource_async(self._url, request, self._finish)

    def _finish(self, response, error):
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
        self.responseText = response._body_value()
        self.response = self.responseText
        self._state(self.HEADERS_RECEIVED)
        self._state(self.LOADING)
        self._state(self.DONE)
        self.fn_dispatchEvent(Event("load"))
        self.fn_dispatchEvent(Event("loadend"))

    def fn_abort(self):
        self.readyState = self.UNSENT
        self.fn_dispatchEvent(Event("abort"))
        self.fn_dispatchEvent(Event("loadend"))


__all__ = ["XMLHttpRequestEventTarget", "XMLHttpRequest"]
