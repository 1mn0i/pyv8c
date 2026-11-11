"""The cycronet engine: one optional client that can serve both roles.

Naming it for both roles shares one instance, so requests and sockets share one
connection cache.
"""
import importlib
import inspect
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from ..foundation.logging import logger  # patch target for cookie-change logging tests

from ..foundation.host_errors import HostStateError
from ..foundation.tasks import TaskSource
from .backend import RequestDelivery
from .cookies import CookieJar
from .models import (
    NetworkRequest,
    NetworkResponse,
    NetworkUnavailableError,
    cookie_header,
    permits_credentials,
)
from .redirects import Deliver, next_hop
from .proxy import PROXY_OPTION
from .registry import signature_option_keys


def _cycronet_module():
    """The optional cycronet package, or an error naming what is missing."""
    try:
        return importlib.import_module("cycronet")
    except ImportError as exc:
        raise NetworkUnavailableError("the 'cronet' engine requires the optional 'cycronet' package") from exc


def _cycronet_client_type(module):
    client_type = getattr(module, "CronetClient", None)
    if client_type is None:
        client_type = getattr(module, "PyCronetClient", None)
    if client_type is None:
        raise NetworkUnavailableError("installed cycronet does not expose CronetClient or PyCronetClient")
    return client_type


class CronetTransport(RequestDelivery):
    """Owns one optional cycronet client, which serves both engine roles.

    The same client performs requests and opens sockets, so naming cycronet for
    both roles hands a context one instance.  It never touches V8: what it
    delivers goes through the task capability it was given.
    """

    #: Resource loading inspects redirects and reapplies cookie policy on each hop.
    FOLLOWS_ITSELF = False

    @classmethod
    def accepted_option_keys(cls, kind: str = "http") -> frozenset[str] | None:
        """The keys the *kind* role's options may carry: the client's, plus ours.

        Leftovers go straight to the installed client, so its constructor is the list;
        the request role owns a thread pool and both roles take the context's proxy.
        """
        client_keys = signature_option_keys(_cycronet_client_type(_cycronet_module()))
        ours = {PROXY_OPTION, "max_workers"} if kind == "http" else {PROXY_OPTION}
        if client_keys is None:
            return None
        return client_keys | ours

    def __init__(self, *, options=None):
        self.options = dict(options or {})
        # Ours, not the client's: consumed here, and the proxy renamed on the way.
        max_workers = int(self.options.pop("max_workers", 4))
        proxy = self.options.pop(PROXY_OPTION, None)
        if proxy and "proxies" not in self.options:
            self.options["proxies"] = proxy
        self.cycronet = _cycronet_module()
        self.client = self._new_client()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pyv8c-cronet")
        self.cookies = CookieJar()
        self._websockets = set()
        self._closed = False
        self._lock = RLock()

    def _new_client(self): return _cycronet_client_type(self.cycronet)(**self.options)

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
        raw_headers = getattr(raw, '_headers', None)
        if hasattr(raw_headers, 'items'):
            for name, values in raw_headers.items():
                if str(name).lower() == 'set-cookie' and isinstance(values, (list, tuple)):
                    headers['set-cookie'] = [str(value) for value in values]
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
        if request.redirect != 'follow':
            return self._single_call(request)
        current = request
        hops = 0
        while True:
            response = self._single_call(current)
            decision = next_hop(status=response.status, method=current.method,
                                url=current.url, headers=current.headers, body=current.body,
                                location=response.headers.get('location'), hops=hops,
                                mode=current.mode, redirect='follow')
            if isinstance(decision, Deliver):
                response.redirected = bool(hops) or response.redirected
                return response
            headers = dict(decision.headers)
            if decision.cross_origin:
                headers = {k: v for k, v in headers.items() if k.lower() != 'cookie'}
            current = replace(current, url=decision.url, method=decision.method,
                              body=decision.body, headers=headers)
            hops += 1

    def _single_call(self, request: NetworkRequest):
        if self._closed:
            raise HostStateError("network backend is closed")
        logger.debug("network download: {} {}", request.method, request.url)
        headers = dict(request.headers)
        cookie = cookie_header(self.cookies, request)
        if "cookie" not in {str(name).lower() for name in headers}:
            # cycronet otherwise injects its own session jar, bypassing credentials
            # and SameSite filtering. An explicit empty header suppresses it.
            headers["Cookie"] = cookie
        method = request.method.lower()
        fn = getattr(self.client, method, None)
        if fn is None:
            fn = getattr(self.client, "request", None)
            if fn is None:
                raise NetworkUnavailableError(f"cycronet client has no {method} method")
            raw = fn(request.method, request.url, headers=headers, data=request.body, timeout=request.timeout,
                     allow_redirects=False)
        else:
            kwargs = {"headers": headers, "allow_redirects": False}
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
        if response.redirected:
            raise NetworkUnavailableError('cycronet followed a redirect despite allow_redirects=False')
        set_cookie = response.headers.get("set-cookie")
        if set_cookie and permits_credentials(request):
            self.cookies.add_from_response(response.url, set_cookie)
        return response

    def websocket(self, page, url, protocols, callbacks, options=None):
        if self._closed:
            raise HostStateError("network backend is closed")
        factory = getattr(self.client, "websocket", None)
        if factory is None:
            raise NetworkUnavailableError("installed cycronet does not expose websocket callback API")
        callbacks = dict(callbacks)
        def deliver(name, *arguments):
            handler = callbacks.get(name)
            if handler is not None:
                page.post_task(lambda: handler(*arguments), TaskSource.NETWORKING)

        kwargs = {"on_open": lambda ws: deliver("open", ws),
                  "on_message": lambda ws, message, is_text=True: deliver("message", ws, message, is_text),
                  "on_close": lambda ws, code, reason, was_clean=True: deliver("close", ws, code, reason, was_clean),
                  "on_error": lambda ws, error, net_error=None: deliver("error", ws, error, net_error)}
        try:
            ws = factory(url, protocols=protocols or [], **kwargs)
        except TypeError:
            ws = factory(url, **kwargs)
        with self._lock:
            self._websockets.add(ws)
        if hasattr(ws, "run_in_background"):
            ws.run_in_background()
        return ws

    def close_websocket(self, websocket):
        try:
            websocket.close(1000, "page closed")
        except Exception:
            pass
        with self._lock:
            self._websockets.discard(websocket)

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
            except Exception:  # teardown must not raise
                pass
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()


__all__ = ["CronetTransport"]
