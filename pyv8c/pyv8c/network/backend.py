"""One context's network stack: an HTTP engine and a WebSocket engine.

Both are handed the context's single cookie jar, so a document, its requests and
its sockets are one session as far as a server is concerned.
"""
from threading import RLock

from .models import NetworkUnavailableError, RequestHandle


class RequestDelivery:
    """The delivery an engine with a pool performs for a host request.

    Engines produce transport-neutral results. The caller queues them on the
    owner; this layer owns only pool work, cancellation and in-flight counts.
    """

    def request_sync(self, request):
        return self._call(request)

    def request_async(self, tasks, request, callback):
        handle = RequestHandle()
        submission_error = None
        with self._lock:
            if self._closed:
                closed = True
            else:
                closed = False
                tasks.begin_network()
                try:
                    future = self.executor.submit(self._call, request)
                except BaseException as exc:
                    submission_error = exc
                else:
                    handle.future = future

        # Never invoke a caller while holding the engine's lifecycle lock.
        if closed:
            callback(None, RuntimeError("network backend is closed"))
            return handle

        if submission_error is not None:
            try:
                if not handle.cancelled and not tasks.closed:
                    callback(None, submission_error)
            finally:
                tasks.complete_network()
            return handle

        def complete(done):
            try:
                result, error = done.result(), None
            except BaseException as exc:  # delivered to the caller, not raised here
                result, error = None, exc
            try:
                if not handle.cancelled and not tasks.closed:
                    callback(result, error)
            finally:
                # Transport accounting must not depend on the owner accepting
                # or eventually executing a completion task.
                tasks.complete_network()
        future.add_done_callback(complete)
        return handle


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
                "this context has no HTTP engine; set http_engine= or real_network=True"
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
