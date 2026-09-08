"""XMLHttpRequest implementation."""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

import pyv8



from ...engine.registry import interface
from ...foundation.errors import InvalidStateError, NotSupportedError
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
            AttributeSpec(
                name="responseText", getter="get_responseText", side_effect=1
            ),
            AttributeSpec(name="response", getter="get_response", side_effect=1),
        ),
        methods=(
            MethodSpec(name="open", callback="fn_open", length=2),
            MethodSpec(name="send", callback="fn_send"),
            MethodSpec(
                name="setRequestHeader", callback="fn_setRequestHeader", length=2
            ),
            MethodSpec(
                name="getResponseHeader",
                callback="fn_getResponseHeader",
                length=1,
                side_effect=1,
            ),
            MethodSpec(name="abort", callback="fn_abort"),
        ),
        constructor_behavior=1,
        has_constructor=1,
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
