"""One context's network stack: an HTTP engine and a WebSocket engine.

The two are chosen independently (``network_engine`` and ``ws_engine``, see
``registry.py``), so a context can have either, both, or -- with networking off
-- neither.  This is what the rest of the engine holds: requests go to the HTTP
engine, sockets to the WebSocket engine, and a call for an engine that was never
assembled is refused with an error naming what to set.

Both engines are handed the context's single cookie jar, so a document, its
requests and its sockets are one session as far as a server is concerned.
"""
from threading import RLock

from .models import NetworkUnavailableError


def _distinct(*parts):
    """The parts, each once: one engine may be serving both roles."""
    seen = []
    for part in parts:
        if part is not None and all(part is not other for other in seen):
            seen.append(part)
    return seen


class NetworkBackend:
    """The engines of one context; an absent one refuses the calls that need it."""

    def __init__(self, http=None, ws=None):
        self._http = http
        self._ws = ws
        self._cookies = None
        self._closed = False
        self._lock = RLock()

    @property
    def http_available(self) -> bool: return self._http is not None

    @property
    def ws_available(self) -> bool: return self._ws is not None

    @property
    def http(self):
        """The HTTP engine, or ``None`` when only sockets were assembled."""
        return self._http

    @property
    def ws(self):
        """The WebSocket engine, or ``None`` when only requests were assembled."""
        return self._ws

    @property
    def cookies(self):
        """The context's cookie jar, which both engines share."""
        return self._cookies

    @cookies.setter
    def cookies(self, jar):
        self._cookies = jar
        for part in _distinct(self._http, self._ws):
            part.cookies = jar

    def request_sync(self, request): return self._http_engine().request_sync(request)

    def request_async(self, tasks, request, callback):
        return self._http_engine().request_async(tasks, request, callback)

    def _http_engine(self):
        if self._http is None:
            raise NetworkUnavailableError(
                "this context has no HTTP engine; set network_engine= or real_network=True"
            )
        return self._http

    def websocket(self, tasks, url, protocols, callbacks, options=None):
        return self._ws_engine().websocket(tasks, url, protocols, callbacks, options)

    def close_websocket(self, websocket):
        """Close one socket; a context with no socket engine has none to close."""
        engine = self._ws
        if engine is not None:
            engine.close_websocket(websocket)

    def _ws_engine(self):
        if self._ws is None:
            raise NetworkUnavailableError(
                "this context has no WebSocket engine; set ws_engine= or real_network=True"
            )
        return self._ws

    def close(self):
        """Close both engines; safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            http, ws = self._http, self._ws
            self._http = self._ws = None
        for part in _distinct(http, ws):
            try:
                part.close()
            except Exception:  # one engine failing must not strand the other
                pass


__all__ = ["NetworkBackend"]
