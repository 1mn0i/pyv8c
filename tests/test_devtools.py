import asyncio
import socket
import unittest

import pyv8
from pyv8.devtools import DevtoolsDebugger
from websockets.asyncio.client import connect


class TestDevtools(unittest.TestCase):
    def test_websocket_bridge(self):
        class Window:
            pass

        context = pyv8.Context(Window())
        debugger = DevtoolsDebugger(context)
        host = "127.0.0.1"

        sock = socket.socket()
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        sock.close()

        debugger.start_server(host, port)

        async def run_client():
            async with connect(f"ws://{host}:{port}/") as websocket:
                await websocket.send("ping")
                self.assertEqual(debugger.top_queue.get(timeout=2), "ping")

                debugger.handle("pong")
                message = await asyncio.wait_for(websocket.recv(), timeout=2)
                self.assertEqual(message, "pong")

        try:
            asyncio.run(run_client())
        finally:
            debugger.close()
