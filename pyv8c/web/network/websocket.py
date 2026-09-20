"""WebSocket Web API backed by the network transport service."""
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import FeatureImplementation, interface
from ...foundation.arguments import (
    MISSING,
    dom_string,
    event_handler,
    integer,
    is_nullish,
    missing_error,
    sequence,
    usv_string,
)
from ...foundation.errors import InvalidStateError, V8SyntaxError, dom_exception
from ..dom.events import Event, EventTarget
from ..fetch.blob import Blob, bytes_from_value
from ..fetch.events import CloseEvent, ErrorEvent, MessageEvent
from .models import NetworkUnavailableError


@interface('WebSocket', 'EventTarget')
class WebSocket(EventTarget):
    CONNECTING, OPEN, CLOSING, CLOSED = 0, 1, 2, 3
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="url", getter="get_url", side_effect=1),
            AttributeSpec(name="protocol", getter="get_protocol", side_effect=1),
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="extensions", getter="get_extensions", side_effect=1),
            AttributeSpec(name="binaryType", getter="get_binaryType", setter="set_binaryType"),
            AttributeSpec(name="bufferedAmount", getter="get_bufferedAmount", side_effect=1),
            AttributeSpec(name="onopen", getter="get_onopen", setter="set_onopen"),
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
            AttributeSpec(name="onclose", getter="get_onclose", setter="set_onclose"),
        ),
        methods=(
            MethodSpec(name="send", callback="fn_send", length=1),
            MethodSpec(name="close", callback="fn_close", length=2),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, url=MISSING, protocols=pyv8.Undefined, *_extra, _realm=None, **kw):
        if url is MISSING:
            raise missing_error('WebSocket', 'constructor', 1, 0, constructor=True)
        url = usv_string(url)
        if protocols is pyv8.Undefined or protocols is None:
            protocols = []
        elif isinstance(protocols, pyv8.JSObject):
            iterator = protocols._iterator_method()
            protocols = (
                [dom_string(protocols)] if is_nullish(iterator)
                else sequence(protocols, dom_string, iterator_method=iterator)
            )
        elif isinstance(protocols, (list, tuple)):
            protocols = sequence(protocols, dom_string)
        else:
            protocols = [dom_string(protocols)]
        services = _realm
        base = services.realm_services.url if services else "about:blank"
        try:
            parts = urlsplit(urljoin(base, url))
            scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
            if scheme not in ("ws", "wss") or not parts.netloc or parts.fragment:
                raise ValueError("invalid WebSocket URL")
            # Reading the port is what rejects a URL whose port cannot be one.
            _ = parts.port
            url = urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
        except ValueError as exc:
            raise V8SyntaxError("Failed to construct 'WebSocket': The URL is invalid.") from exc
        if len(set(protocols)) != len(protocols) or any(
            not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", protocol) for protocol in protocols
        ):
            raise V8SyntaxError("Failed to construct 'WebSocket': Invalid or duplicate subprotocol.")
        super().__init__(services=services, **kw)
        if not services or not services.context_services.ws_available:
            raise NetworkUnavailableError(
                "WebSocket requires a WebSocket engine; set ws_engine= or real_network=True"
            )
        self._services, self._url = services, str(url)
        self._protocol = self._extensions = ""
        self._state_value, self._binary_type, self._buffered = self.CONNECTING, "blob", 0
        self._ws = None
        callbacks = {"open": self._on_open, "message": self._on_message,
                     "error": self._on_error, "close": self._on_close}
        self._ws = services.context_services.websocket(self._url, protocols, callbacks)
        services.context_services.track_websocket(self._ws)

    def get_url(self): return self._url
    def get_protocol(self): return self._protocol
    def get_readyState(self): return self._state_value
    def get_extensions(self): return self._extensions
    def get_binaryType(self): return self._binary_type

    def set_binaryType(self, value):
        value = dom_string(value)
        if value in ("blob", "arraybuffer"):
            self._binary_type = value

    def get_bufferedAmount(self): return self._buffered
    def get_onopen(self): return self._get_handler("onopen")
    def set_onopen(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onopen', value)
    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onmessage', value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onerror', value)
    def get_onclose(self): return self._get_handler("onclose")
    def set_onclose(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._set_handler('onclose', value)

    def fn_send(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('WebSocket', 'send', 1, 0, constructor=False)
        if self._state_value != self.OPEN:
            raise InvalidStateError("WebSocket is not open")
        binary = bytes_from_value(value)
        if binary is not None and not isinstance(value, str) and hasattr(self._ws, "send_bytes"):
            self._ws.send_bytes(binary)
        else:
            self._ws.send(str(value))

    def fn_close(self, code=1000, reason='', *_extra):
        if code is pyv8.Undefined:
            code = 1000
        if code is not pyv8.Undefined:
            code = integer(code, bits=16)
        if reason is pyv8.Undefined:
            reason = ''
        if reason is not pyv8.Undefined:
            reason = usv_string(reason)
        if code != 1000 and not 3000 <= code <= 4999:
            raise dom_exception("InvalidAccessError", "The code must be either 1000, or between 3000 and 4999.")
        if len(reason.encode("utf-8")) > 123:
            raise V8SyntaxError("The close reason must not be greater than 123 UTF-8 bytes.")
        if self._state_value in (self.CLOSING, self.CLOSED):
            return
        self._state_value = self.CLOSING
        if self._ws:
            self._ws.close(int(code), str(reason))

    def _on_open(self, ws):
        self._state_value = self.OPEN
        self._protocol = getattr(ws, "protocol", "") or ""
        self.fn_dispatchEvent(Event("open"))

    def _on_message(self, ws, data, is_text=True):
        if is_text:
            value = str(data)
        elif self._binary_type == "blob":
            value = Blob([bytes_from_value(data) or b""])
        else:
            value = bytes_from_value(data) or b""
        self.fn_dispatchEvent(MessageEvent("message", {
            "data": value, "origin": self._services.realm_services.url,
            "source": self._services.document_services.window,
        }))

    def _on_error(self, ws, error, net_error=None):
        self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error)}))

    def _on_close(self, ws, code, reason, was_clean=True):
        self._state_value = self.CLOSED
        self.fn_dispatchEvent(CloseEvent("close", {
            "code": code, "reason": reason, "wasClean": was_clean,
        }))
        self._services.context_services.discard_websocket(ws)


__all__ = ["WebSocket"]


FEATURE = FeatureImplementation({"WebSocket": WebSocket})
