"""The ``websockets`` engine: the page's sockets on a real WebSocket client.

It is the default engine, so ``new WebSocket`` works with nothing optional installed.
Each socket gets its own thread running ``websockets.sync``, because a blocking
connection matches the project's thread model.
"""
import importlib
import inspect
import queue
import socket as sockets
import threading
from threading import RLock

from ..foundation.logging import logger
from ..foundation.tasks import TaskSource

from ..foundation.urls import header_set as _header_set
from .models import NetworkUnavailableError
from .proxy import PROXY_OPTION, websockets_proxy
from .socket_lifetime import OwnedSocket

#: Options the engine consumes itself: handshake ingredients and the proxy, translated before passing on.
_BRIDGE_KEYS = frozenset({"cookies", "headers", PROXY_OPTION})

#: Keyword names the engine supplies itself; the target and handshake are its own.
_MANAGED_KEYS = frozenset({"uri", "subprotocols", "additional_headers", "logger", "origin"})


def _connect_client():
    """``websockets.sync``'s ``connect``, or an error naming what is missing."""
    try:
        module = importlib.import_module("websockets.sync.client")
    except ImportError as exc:
        raise NetworkUnavailableError(
            "the websockets network engine requires the 'websockets' package"
        ) from exc
    return module.connect


def _owned_connection(raw_socket, *args, **kwargs):
    """Own the handle before ClientConnection starts its receiver thread."""
    module = importlib.import_module("websockets.sync.client")
    return module.ClientConnection(OwnedSocket(raw_socket), *args, **kwargs)


def _close_details(exc):
    """The close a raised connection reported, or ``None`` if it reported none."""
    received = getattr(exc, "rcvd", None)
    if received is not None:
        code = int(received.code)
        return code, str(received.reason), code in (1000, 1001)
    sent = getattr(exc, "sent", None)
    if sent is not None:
        return int(sent.code), str(sent.reason), True
    return None


def _recorded_close(connection):
    """The close the protocol recorded, or ``None`` if it recorded none."""
    code = getattr(connection, "close_code", None)
    if code is None:
        return None
    return int(code), str(getattr(connection, "close_reason", "") or ""), True


def _allows_cookies(url):
    """The URL whose cookies a handshake to *url* carries."""
    for scheme, equivalent in (("wss://", "https://"), ("ws://", "http://")):
        if url.startswith(scheme):
            return equivalent + url[len(scheme):]
    return url


class _Socket:
    """One connection, as the page's WebSocket API sees it."""

    #: The negotiated subprotocol; the page reads it when the socket opens.
    protocol = ""

    def __init__(self, tasks, url, protocols, callbacks, connect_kwargs, *, on_finished=None):
        self._tasks = tasks
        self._url = url
        self._protocols = protocols
        self._callbacks = dict(callbacks)
        self._connect_kwargs = connect_kwargs
        self._connection = None
        self._close_requested = None
        self._io_thread = None
        self._writes = queue.SimpleQueue()
        self._buffered_amount = 0
        self._write_error = None
        self._disposed = False
        self._dispose_abort = False
        self._io_active = False
        self._open_delivered = False
        self._cancelled_before_open = False
        self._error_delivered = False
        self._on_finished = on_finished
        self._lock = RLock()
        self._thread = threading.Thread(target=self._run, name="pyv8c-websocket", daemon=True)

    def send(self, message): self._send(message)
    def send_bytes(self, data): self._send(data)
    def close(self, code=1000, reason=""):
        with self._lock:
            if self._close_requested is not None:
                return
            self._close_requested = (int(code), str(reason))
            self._cancelled_before_open = not self._open_delivered
            connection = self._connection
            if connection is None:
                return
            try:
                self._queue_write(connection, ("close", self._close_requested, 0))
            except BaseException:
                self._close_requested = None
                self._cancelled_before_open = False
                raise

    @staticmethod
    def _close_connection(connection, request):
        """The sync client's close waits for the peer; never run it on owner."""
        try:
            connection.close(*request)
        except Exception as exc:
            logger.debug("websocket close failed: {}", exc)

    def dispose(self):
        """Release realm callbacks before requesting transport shutdown."""
        with self._lock:
            self._callbacks.clear()
            if not self._disposed:
                self._dispose_abort = self._io_active or self._close_requested is not None
            self._disposed = True
            connection = self._connection
            raw_socket = getattr(connection, "socket", None) if self._dispose_abort else None
            if raw_socket is not None:
                # A graceful close queued behind blocked I/O cannot cancel it.
                # shutdown doesn't acquire the client's protocol/send mutex.
                if self._close_requested is None:
                    self._close_requested = (1000, "owner closed")
                self._stop_writer()
        if raw_socket is not None:
            abort = getattr(raw_socket, "abort", None)
            if abort is not None:
                abort()
                return
            try:
                raw_socket.shutdown(sockets.SHUT_RDWR)
            except OSError as exc:
                logger.debug("websocket shutdown: {}", exc)
            finally:
                raw_socket.close()
            return
        self.close(1000, "owner closed")

    def _send(self, payload):
        size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        with self._lock:
            connection = self._connection
            if self._disposed or connection is None or self._close_requested is not None or self._write_error is not None:
                logger.debug("websocket send dropped: the socket is not open")
                return
            self._queue_write(connection, ("send", payload, size))
            self._buffered_amount += size

    @property
    def buffered_amount(self):
        with self._lock:
            return self._buffered_amount

    def _queue_write(self, connection, operation):
        """Called under the socket lock; one lazy writer preserves send/close FIFO."""
        if self._io_thread is None:
            worker = threading.Thread(target=self._write, args=(connection,),
                                      name="pyv8c-websocket-io", daemon=True)
            self._io_thread = worker
            try:
                worker.start()
            except BaseException:
                self._io_thread = None
                raise
        self._writes.put(operation)

    def _write(self, connection):
        while (operation := self._writes.get()) is not None:
            kind, payload, size = operation
            with self._lock:
                if kind == "send" and self._disposed:
                    del operation, payload
                    continue
                self._io_active = True
            if kind == "close":
                try:
                    self._close_connection(connection, payload)
                finally:
                    with self._lock:
                        self._io_active = False
                return
            try:
                connection.send(payload)
                self._tasks.post_task(lambda size=size: self._acknowledge(size), TaskSource.NETWORKING)
            except Exception as exc:
                with self._lock:
                    if self._connection is connection:
                        self._write_error = exc.with_traceback(None)
                logger.debug("websocket send failed: {}", exc)
                self._close_connection(connection, (1000, ""))
                return
            finally:
                with self._lock:
                    self._io_active = False
            # Don't retain the last (possibly large) message while get() waits.
            del operation, payload

    def _acknowledge(self, size):
        with self._lock:
            self._buffered_amount -= size

    def _stop_writer(self):
        with self._lock:
            while True:
                try:
                    self._writes.get_nowait()
                except queue.Empty:
                    break
            self._writes.put(None)

    def start(self): self._thread.start()

    def _run(self):
        try:
            self._run_connection()
        finally:
            self._stop_writer()
            with self._lock:
                self._write_error = None
                on_finished, self._on_finished = self._on_finished, None
            if on_finished is not None:
                on_finished(self)

    def _run_connection(self):
        self._tasks.begin_network()
        try:
            connection = _connect_client()(
                self._url, subprotocols=self._protocols or None, **self._connect_kwargs
            )
        except Exception as exc:
            logger.debug("websocket failed to open {}: {!r}", self._url, exc)
            self._deliver("error", self, exc)
            self._deliver("close", self, 1006, "", False)
            return
        finally:
            self._tasks.complete_network()
        with self._lock:
            cancelled = self._close_requested
            if cancelled is None:
                self._connection = connection
                self.protocol = str(getattr(connection, "subprotocol", "") or "")
        if cancelled is not None:
            try:
                connection.close(*cancelled)
            except Exception as exc:
                logger.debug("websocket cancelled handshake cleanup failed: {}", exc)
            self._deliver("error", self, ConnectionAbortedError("WebSocket closed during handshake"))
            self._deliver("close", self, 1006, "", False)
            return
        logger.debug("websocket open {} as {}", self._url, self.protocol or "no subprotocol")
        details = None
        read_error = None
        try:
            with connection:
                self._deliver("open", self)
                for message in connection:
                    self._deliver("message", self, message, isinstance(message, str))
        except Exception as exc:  # a connection ends by raising
            details = _close_details(exc)
            if details is None:
                logger.debug("websocket failed on {}: {!r}", self._url, exc)
                read_error = exc
        finally:
            with self._lock:
                self._connection = None
                write_error = self._write_error
            if write_error is not None or read_error is not None:
                self._deliver("error", self, write_error or read_error)
            if write_error is not None:
                details = (1006, "", False)
            self._deliver("close", self, *(details or _recorded_close(connection) or (1006, "", False)))

    def _deliver(self, name, *arguments):
        """Hand one event to the page, on the realm's own task queue."""
        with self._lock:
            if name not in self._callbacks:
                return

        def deliver():
            with self._lock:
                cancelled = self._cancelled_before_open
                if cancelled and name in ("open", "message"):
                    return
                handler = self._callbacks.get(name)
                if handler is None:
                    return
                if name == "open":
                    self._open_delivered = True
                if name == "error":
                    if cancelled and self._error_delivered:
                        return
                    self._error_delivered = True
                inject_error = name == "close" and cancelled and not self._error_delivered
                error_handler = self._callbacks.get("error") if inject_error else None
                if inject_error:
                    self._error_delivered = True
            delivered_arguments = (self, 1006, "", False) if name == "close" and cancelled else arguments
            if inject_error:
                # Failure can be requested after the transport already queued
                # a clean close. Preserve a callback/checkpoint before close.
                try:
                    if error_handler is not None:
                        error_handler(self, ConnectionAbortedError("WebSocket closed before open delivery"))
                finally:
                    self._deliver("close", *delivered_arguments)
                return
            try:
                handler(*delivered_arguments)
            finally:
                if name == "close":
                    with self._lock:
                        self._callbacks.clear()

        self._tasks.post_task(deliver, TaskSource.NETWORKING)


class WebSocketsTransport:
    """Opens WebSockets with ``websockets``; one connection per thread."""

    #: The context's cookie jar; a handshake carries what a request to the URL would.
    cookies = None

    @classmethod
    def accepted_option_keys(cls) -> frozenset[str] | None:
        """The keys ``ws_options`` may carry."""
        try:
            parameters = inspect.signature(_connect_client()).parameters
        except (TypeError, ValueError):
            return None
        named = {
            name for name, parameter in parameters.items()
            if parameter.kind not in (
                parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL, parameter.POSITIONAL_ONLY,
            )
        }
        return (frozenset(named) - _MANAGED_KEYS) | _BRIDGE_KEYS

    def __init__(self, *, options=None):
        # Fail during backend construction instead of turning a missing client
        # into an asynchronous WebSocket close event.
        _connect_client()
        self.options = dict(options or {})
        self.cookies = None
        self._sockets = set()
        self._closed = False
        self._lock = RLock()

    def websocket(self, tasks, url, protocols, callbacks, options=None):
        """Open one socket and start delivering its events on *tasks*' queue."""
        with self._lock:
            if self._closed:
                raise NetworkUnavailableError("this WebSocket engine is closed")
            socket = _Socket(
                tasks, str(url), list(protocols or []), callbacks,
                self._connect_kwargs(str(url), options),
                on_finished=self._discard_socket,
            )
            self._sockets.add(socket)
            try:
                socket.start()
            except BaseException:
                self._sockets.discard(socket)
                socket.dispose()
                raise
        return socket

    def _discard_socket(self, socket):
        with self._lock:
            self._sockets.discard(socket)

    def close_websocket(self, websocket):
        with self._lock:
            self._sockets.discard(websocket)
        websocket.dispose()

    def _connect_kwargs(self, url, given):
        """The ``connect`` arguments for one socket, handshake and proxy included."""
        call = dict(given or {})
        kwargs = {name: value for name, value in self.options.items() if name not in _BRIDGE_KEYS}
        if kwargs.get("create_connection") is None:
            kwargs["create_connection"] = _owned_connection
        # Always given: the client's default ``True`` reads the environment, but this context connects directly.
        kwargs["proxy"] = websockets_proxy(self.options.get(PROXY_OPTION))
        user_agent, headers = self._handshake_headers(url, call)
        if user_agent is not None:
            kwargs.setdefault("user_agent_header", user_agent)
        if headers:
            kwargs.setdefault("additional_headers", headers)
        return kwargs

    def _handshake_headers(self, url, call):
        """The headers one handshake carries: the page's, then the configured ones."""
        headers = {}
        user_agent = None
        for layer in (call.get("headers"), self.options.get("headers")):
            for name, value in dict(layer or {}).items():
                lowered = str(name).lower()
                headers = {key: item for key, item in headers.items() if str(key).lower() != lowered}
                if lowered == "user-agent":
                    user_agent = str(value)
                else:
                    headers[name] = value
        if call.get("origin"):
            _header_set(headers, "Origin", str(call["origin"]))
        if self.options.get("cookies", True) and self.cookies is not None:
            cookie = self.cookies.header_for(_allows_cookies(url))
            if cookie:
                _header_set(headers, "Cookie", cookie)
        return user_agent, headers

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._sockets)
            self._sockets.clear()
        for socket in sockets:
            socket.dispose()


__all__ = ["WebSocketsTransport"]
