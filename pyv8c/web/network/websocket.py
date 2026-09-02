"""WebSocket Web API backed by the network transport service."""

from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.errors import InvalidStateError, V8SyntaxError
from ..dom.events import Event, EventTarget
from ..fetch.blob import Blob, bytes_from_value
from ..fetch.events import CloseEvent, ErrorEvent, MessageEvent
from .backend import NetworkUnavailableError


@interface("WebSocket", "EventTarget", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class WebSocket(EventTarget):
    CONNECTING, OPEN, CLOSING, CLOSED = 0, 1, 2, 3
    __v8_attribute__ = js_attributes(
        ("url", "get_url", None, 0, 1), ("protocol", "get_protocol", None, 0, 1),
        ("readyState", "get_readyState", None, 0, 1), ("extensions", "get_extensions", None, 0, 1),
        ("binaryType", "get_binaryType", "set_binaryType", 0, 0),
        ("bufferedAmount", "get_bufferedAmount", None, 0, 1),
        ("onopen", "get_onopen", "set_onopen", 0, 0),
        ("onmessage", "get_onmessage", "set_onmessage", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
        ("onclose", "get_onclose", "set_onclose", 0, 0),
    )
    __v8_method__ = js_methods(("send", "fn_send", 1, 0), ("close", "fn_close", 2, 0))

    def __init__(self, url="", protocols=None, _realm=None, **kw):
        page = _realm
        super().__init__(page=page, **kw)
        if not page or not page.network_available:
            raise NetworkUnavailableError("WebSocket requires real_network=True")
        self._page, self._url = page, str(url)
        self._protocol = self._extensions = ""
        self._state_value, self._binary_type, self._buffered = self.CONNECTING, "blob", 0
        self._ws, self._protocols = None, protocols
        callbacks = {"open": self._on_open, "message": self._on_message,
                     "error": self._on_error, "close": self._on_close}
        self._ws = page.websocket(self._url, protocols, callbacks)
        page._track_websocket(self._ws)

    def get_url(self): return self._url
    def get_protocol(self): return self._protocol
    def get_readyState(self): return self._state_value
    def get_extensions(self): return self._extensions
    def get_binaryType(self): return self._binary_type

    def set_binaryType(self, value):
        if str(value) not in ("blob", "arraybuffer"):
            raise V8SyntaxError("invalid binaryType")
        self._binary_type = str(value)

    def get_bufferedAmount(self): return self._buffered
    def get_onopen(self): return self._get_handler("onopen")
    def set_onopen(self, value): self._set_handler("onopen", value)
    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value): self._set_handler("onmessage", value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value): self._set_handler("onerror", value)
    def get_onclose(self): return self._get_handler("onclose")
    def set_onclose(self, value): self._set_handler("onclose", value)

    def fn_send(self, value):
        if self._state_value != self.OPEN:
            raise InvalidStateError("WebSocket is not open")
        binary = bytes_from_value(value)
        if binary is not None and not isinstance(value, str) and hasattr(self._ws, "send_bytes"):
            self._ws.send_bytes(binary)
        else:
            self._ws.send(str(value))

    def fn_close(self, code=1000, reason=""):
        if self._state_value in (self.CLOSING, self.CLOSED): return
        self._state_value = self.CLOSING
        if self._ws: self._ws.close(int(code), str(reason))

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
            "data": value, "origin": self._page.url, "source": self._page.window,
        }))

    def _on_error(self, ws, error, net_error=None):
        self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error)}))

    def _on_close(self, ws, code, reason, was_clean=True):
        self._state_value = self.CLOSED
        self.fn_dispatchEvent(CloseEvent("close", {
            "code": code, "reason": reason, "wasClean": was_clean,
        }))
        self._page._discard_websocket(ws)


__all__ = ["WebSocket"]
