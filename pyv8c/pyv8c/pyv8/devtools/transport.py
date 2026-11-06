"""Single-client WebSocket and HTTP discovery transport; never enters V8."""

import asyncio
from http import HTTPStatus
import json
import threading
from urllib.parse import urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


class Transport:
    def __init__(self, router, options):
        self.router = router
        self.options = options
        self.host = options.host
        self.port = None
        self.loop = None
        self.websocket = None
        self.outgoing = None
        self.generation = 0
        self.connected = threading.Event()
        self.state_changed = threading.Condition()
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.error = None
        self.shutdown = None
        self.thread = threading.Thread(target=self._run, name="pyv8-devtools", daemon=True)

    def start(self):
        self.thread.start()
        if not self.started.wait(5):
            raise TimeoutError("DevTools transport did not start")
        if self.error:
            raise RuntimeError("DevTools transport failed") from self.error

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self.error = error
        finally:
            self.started.set()
            self.stopped.set()
            with self.state_changed:
                self.state_changed.notify_all()

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.shutdown = asyncio.Event()
        async with serve(self._handle, self.host, self.options.port,
                         process_request=self._http, close_timeout=1) as server:
            self.port = server.sockets[0].getsockname()[1]
            self.started.set()
            await self.shutdown.wait()

    def _http(self, connection, request):
        path = urlsplit(request.path).path
        payload = self.router.discovery(path)
        if payload is not None:
            response = connection.respond(HTTPStatus.OK, json.dumps(payload))
            response.headers["Content-Type"] = "application/json; charset=utf-8"
            return response
        if not self.router.valid_path(path):
            return connection.respond(HTTPStatus.NOT_FOUND, "Unknown DevTools endpoint")
        return None

    async def _handle(self, websocket):
        if self.websocket is not None:
            await websocket.close(1013, "debugger already connected")
            return
        self.websocket = websocket
        self.outgoing = asyncio.Queue()
        self.generation += 1
        generation = self.generation
        self.router.connect(urlsplit(websocket.request.path).path, generation)
        self.connected.set()
        with self.state_changed:
            self.state_changed.notify_all()
        sender = asyncio.create_task(self._sender(websocket, self.outgoing))
        try:
            async for message in websocket:
                self.router.dispatch(message, generation)
        except ConnectionClosed:
            pass
        finally:
            self.router.disconnect(generation)
            self.connected.clear()
            self.websocket = None
            self.outgoing = None
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            with self.state_changed:
                self.state_changed.notify_all()

    def wait_for_connection(self, timeout=None):
        with self.state_changed:
            self.state_changed.wait_for(lambda: self.connected.is_set() or self.stopped.is_set(), timeout)
            return self.connected.is_set() and not self.stopped.is_set()

    async def _sender(self, websocket, outgoing):
        try:
            while True:
                await websocket.send(await outgoing.get())
        except ConnectionClosed:
            return

    def send(self, payload, generation):
        def enqueue():
            if self.outgoing is not None and generation == self.generation:
                self.outgoing.put_nowait(json.dumps(payload, separators=(",", ":")))
        if self.loop is not None and not self.stopped.is_set():
            try:
                self.loop.call_soon_threadsafe(enqueue)
            except RuntimeError:
                if not self.stopped.is_set():
                    raise

    def close(self):
        if self.loop is not None and not self.stopped.is_set():
            self.loop.call_soon_threadsafe(self.shutdown.set)
        if self.thread.ident is not None and threading.current_thread() is not self.thread:
            self.thread.join(5)
            if self.thread.is_alive():
                raise TimeoutError("DevTools transport did not stop")
