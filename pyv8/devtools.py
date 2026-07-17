import asyncio
import queue
import signal
import threading

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pyv8 import _pyv8


class DevtoolsDebugger(_pyv8.Debugger):
    _STOP = object()

    def __init__(self, context, *args, callback=None):
        super().__init__(context)
        self.base_queue = queue.Queue()
        self.queues = [self.base_queue]
        self.queue_lock = threading.RLock()

        self.context = context
        self.callback = callback
        self.args = args

        self.loop = None
        self.server_thread = None
        self.server_started = threading.Event()
        self.server_error = None
        self.shutdown_future = None
        self.shutdown_event = threading.Event()

        self.ws = None
        self.outgoing_queue = None
        self.ws_lock = threading.RLock()

        signal.signal(signal.SIGTERM, self._signal_shutdown)
        signal.signal(signal.SIGINT, self._signal_shutdown)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self._signal_shutdown)

    def _signal_shutdown(self, signum, _) -> None:
        self.shutdown()

    def start_server(self, host, port):
        self.server_thread = threading.Thread(
            target=self._run_server,
            args=(host, port),
            name="pyv8-devtools",
            daemon=True,
        )
        self.server_thread.start()
        self.server_started.wait()
        if self.server_error is not None:
            raise RuntimeError("failed to start devtools server") from self.server_error

    def _run_server(self, host, port):
        try:
            asyncio.run(self._serve(host, port))
        except BaseException as exc:
            self.server_error = exc
            self.server_started.set()
            self._wake_all_queues()
        finally:
            with self.ws_lock:
                self.loop = None
                self.shutdown_future = None

    async def _serve(self, host, port):
        loop = asyncio.get_running_loop()
        shutdown_future = loop.create_future()
        with self.ws_lock:
            self.loop = loop
            self.shutdown_future = shutdown_future

        async with serve(self._handle_websocket, host, port):
            self.server_started.set()
            await shutdown_future

    async def _handle_websocket(self, websocket):
        if self._websocket_path(websocket) != "/":
            await websocket.close(code=1008, reason="unsupported path")
            return

        outgoing_queue = asyncio.Queue()
        already_connected = False
        with self.ws_lock:
            if self.ws is not None:
                already_connected = True
            else:
                self.ws = websocket
                self.outgoing_queue = outgoing_queue

        if already_connected:
            print("Stop and start again")
            await websocket.close(code=1013, reason="debugger already connected")
            return

        sender = asyncio.create_task(self._send_messages(websocket, outgoing_queue))
        try:
            async for message in websocket:
                self.top_queue.put(message)
        finally:
            sender.cancel()
            try:
                await sender
            except asyncio.CancelledError:
                pass
            with self.ws_lock:
                if self.ws is websocket:
                    self.ws = None
                    self.outgoing_queue = None
            self._wake_top_pause_loop()

    @staticmethod
    def _websocket_path(websocket):
        request = getattr(websocket, "request", None)
        path = getattr(request, "path", None)
        if path is None:
            path = getattr(websocket, "path", "/")
        return path.split("?", 1)[0] or "/"

    async def _send_messages(self, websocket, outgoing_queue):
        try:
            while True:
                message = await outgoing_queue.get()
                if message is self._STOP:
                    return
                await websocket.send(message)
        except ConnectionClosed:
            return

    def handle(self, message):
        with self.ws_lock:
            loop = self.loop
            outgoing_queue = self.outgoing_queue
        if loop is None or outgoing_queue is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(outgoing_queue.put_nowait, message)

    def run_js(self):
        if self.callback:
            self.callback(self.context, *self.args)
        else:
            def print_hello():
                print("Hello pyv8env! in python")
                return "Hello pyv8env! in v8"

            self.context.expose(print_hello)
            self.context.exec_js("console.log(print_hello());", "test.js")

    def run_loop(self):
        message_queue = queue.Queue()
        with self.queue_lock:
            self.queues.append(message_queue)
        try:
            self._process_messages(message_queue)
        finally:
            self._remove_queue(message_queue)

    def quit_loop(self):
        self.top_queue.put(self._STOP)

    def _process_messages(self, message_queue):
        while True:
            message = message_queue.get()
            if message is self._STOP:
                return

            top_queue = self.top_queue
            if top_queue is not message_queue:
                top_queue.put(message)
                continue

            if isinstance(message, str):
                message = message.encode("utf-8")
            self.send(message)

    def _remove_queue(self, message_queue):
        with self.queue_lock:
            if message_queue not in self.queues:
                return
            self.queues.remove(message_queue)

    def _wake_top_pause_loop(self):
        with self.queue_lock:
            if len(self.queues) <= 1:
                return
            top_queue = self.queues[-1]
        top_queue.put(self._STOP)

    def _wake_all_queues(self):
        with self.queue_lock:
            queues = list(self.queues)
        for message_queue in queues:
            message_queue.put(self._STOP)

    @property
    def top_queue(self):
        with self.queue_lock:
            return self.queues[-1]

    def wait_for_connect(self):
        self._process_messages(self.base_queue)

    def shutdown(self):
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()

        with self.ws_lock:
            loop = self.loop
            shutdown_future = self.shutdown_future
            outgoing_queue = self.outgoing_queue

        if loop is not None and loop.is_running():
            if outgoing_queue is not None:
                loop.call_soon_threadsafe(outgoing_queue.put_nowait, self._STOP)
            if shutdown_future is not None:
                loop.call_soon_threadsafe(self._finish_future, shutdown_future)

        self._wake_all_queues()

    @staticmethod
    def _finish_future(future):
        if not future.done():
            future.set_result(None)

    def close(self):
        self.shutdown()
        if self.server_thread is not None and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)


def start_devtools(context, *args, callback=None, port=9005, host="127.0.0.1"):
    debugger = DevtoolsDebugger(context, *args, callback=callback)
    debugger.start_server(host, port)
    print("Open Chrome DevTools and enter this URL:")
    print(
        "devtools://devtools/bundled/inspector.html?"
        "experiments=true&v8only=true&ws={}:{}".format(host, port)
    )
    try:
        debugger.wait_for_connect()
    finally:
        debugger.close()


if __name__ == "__main__":
    class Window:
            pass

    c = _pyv8.Context(Window(), ctx_type="Top")
    start_devtools(c)
