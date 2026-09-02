"""Thread-safe HTTP transport bridge for the optional cycronet package."""
from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from urllib.parse import urlsplit
import importlib
import inspect
import time
from loguru import logger
from ...foundation.errors import InvalidStateError
from .cookies import CookieJar


class NetworkUnavailableError(RuntimeError):
    pass


@dataclass
class NetworkRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: object = None
    timeout: float | None = None
    credentials: str = "same-origin"
    mode: str = "cors"
    redirect: str = "follow"
    origin: str | None = None


@dataclass
class NetworkResponse:
    url: str
    status: int = 200
    status_text: str = "OK"
    headers: dict[str, str] = field(default_factory=dict)
    body: object = ""
    redirected: bool = False
    type: str = "basic"


def same_origin(left, right):
    a, b = urlsplit(str(left)), urlsplit(str(right))
    def origin(value):
        port = value.port or (443 if value.scheme == "https" else 80 if value.scheme == "http" else None)
        return value.scheme.lower(), (value.hostname or "").lower(), port
    return origin(a) == origin(b)


class CronetNetworkBackend:
    """Owns one optional cycronet client and never calls V8 from workers."""
    def __init__(self, *, options=None):
        self.options = dict(options or {})
        try:
            self.cycronet = importlib.import_module("cycronet")
        except ImportError as exc:
            raise NetworkUnavailableError("real_network=True requires the optional 'cycronet' package") from exc
        self.client = self._new_client()
        self.executor = ThreadPoolExecutor(max_workers=int(self.options.pop("max_workers", 8)), thread_name_prefix="pyv8c-cronet")
        self.cookies = CookieJar()
        self._websockets = set()
        self._closed = False
        self._lock = RLock()

    def _new_client(self):
        client_type = getattr(self.cycronet, "CronetClient", None)
        if client_type is None:
            client_type = getattr(self.cycronet, "PyCronetClient", None)
        if client_type is None:
            raise NetworkUnavailableError("installed cycronet does not expose CronetClient or PyCronetClient")
        return client_type(**self.options)

    @staticmethod
    def _response(raw, url):
        headers = getattr(raw, "headers", {}) or {}
        if hasattr(headers, "items"):
            headers = {
                str(k).lower(): ([str(item) for item in v] if isinstance(v, (list, tuple)) else str(v))
                for k, v in headers.items()
            }
        else:
            headers = {}
        body = getattr(raw, "content", None)
        if body is None:
            body = getattr(raw, "text", "")
        if callable(body):
            body = body()
        return NetworkResponse(
            str(getattr(raw, "url", None) or url),
            int(getattr(raw, "status_code", getattr(raw, "status", 200))),
            str(getattr(raw, "reason", "")), headers, body,
            bool(getattr(raw, "history", ())),
        )

    def _call(self, request: NetworkRequest):
        if self._closed:
            raise InvalidStateError("network backend is closed")
        headers = dict(request.headers)
        cookie = self.cookies.header_for(request.url) if request.credentials != "omit" and (request.credentials == "include" or not request.origin or same_origin(request.origin, request.url)) else ""
        if cookie and "cookie" not in {k.lower() for k in headers}:
            headers["Cookie"] = cookie
        method = request.method.lower()
        fn = getattr(self.client, method, None)
        if fn is None:
            fn = getattr(self.client, "request", None)
            if fn is None:
                raise NetworkUnavailableError(f"cycronet client has no {method} method")
            raw = fn(request.method, request.url, headers=headers, data=request.body, timeout=request.timeout)
        else:
            kwargs = {"headers": headers}
            if request.body is not None:
                kwargs["data"] = request.body
            if request.timeout is not None:
                try:
                    parameters = inspect.signature(fn).parameters.values()
                    accepts_timeout = any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "timeout"
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    accepts_timeout = True
                if accepts_timeout:
                    kwargs["timeout"] = request.timeout
            raw = fn(request.url, **kwargs)
        response = self._response(raw, request.url)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookies.add_from_response(response.url, set_cookie)
        return response

    def request_sync(self, request: NetworkRequest):
        return self._call(request)

    def request_async(self, page, request, callback):
        with self._lock:
            if self._closed:
                page.post_task(lambda: callback(None, RuntimeError("network backend is closed")))
                return
            page.begin_network()
            future = self.executor.submit(self._call, request)

        def complete(done):
            try:
                result, error = done.result(), None
            except BaseException as exc:
                result, error = None, exc
            if page.closed:
                page.complete_network()
                return
            page.post_task(lambda: (page.complete_network(), callback(result, error)))
        future.add_done_callback(complete)

    def websocket(self, page, url, protocols, callbacks, options=None):
        if self._closed:
            raise InvalidStateError("network backend is closed")
        factory = getattr(self.client, "websocket", None)
        if factory is None:
            raise NetworkUnavailableError("installed cycronet does not expose websocket callback API")
        callbacks = dict(callbacks)
        kwargs = {"on_open": lambda ws: page.post_task(lambda: callbacks.get("open", lambda *_: None)(ws)),
                  "on_message": lambda ws, message, is_text=True: page.post_task(lambda: callbacks.get("message", lambda *_: None)(ws, message, is_text)),
                  "on_close": lambda ws, code, reason, was_clean=True: page.post_task(lambda: callbacks.get("close", lambda *_: None)(ws, code, reason, was_clean)),
                  "on_error": lambda ws, error, net_error=None: page.post_task(lambda: callbacks.get("error", lambda *_: None)(ws, error, net_error))}
        try:
            ws = factory(url, protocols=protocols or [], **kwargs)
        except TypeError:
            ws = factory(url, **kwargs)
        with self._lock:
            self._websockets.add(ws)
        if hasattr(ws, "run_in_background"):
            ws.run_in_background()
        return ws

    def close_websocket(self, ws):
        try:
            ws.close(1000, "page closed")
        except Exception:
            pass
        with self._lock:
            self._websockets.discard(ws)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._websockets)
            self._websockets.clear()
        for ws in sockets:
            try:
                ws.close(1000, "context closed")
            except Exception:
                pass
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()
