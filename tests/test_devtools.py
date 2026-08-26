import asyncio
import socket
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import pyv8
from pyv8 import devtools
from pyv8.devtools import DevtoolsDebugger
from websockets.asyncio.client import connect


class TestDevtools(unittest.TestCase):
    def test_debugger_requires_context_without_crashing(self):
        """验证缺少 Context 时返回 Python 异常，而不是访问未初始化指针。"""
        with self.assertRaises(TypeError):
            pyv8.Debugger()

    def test_run_if_waiting_uses_inspector_callback(self):
        """CDP 方法应由 Inspector 解析，不能通过 JSON 文本搜索触发。"""

        package_root = str(Path(__file__).resolve().parents[1])
        code = f"import sys; sys.path.insert(0, {package_root!r})\n" + textwrap.dedent(
            """
            import json

            import pyv8


            class Window:
                pass


            class ProtocolDebugger(pyv8.Debugger):
                def __init__(self, context):
                    self.run_count = 0
                    super().__init__(context)

                def handle(self, message):
                    pass

                def run_js(self):
                    self.run_count += 1


            context = pyv8.Context(Window())
            debugger = ProtocolDebugger(context)
            debugger.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": "Runtime.runIfWaitingForDebugger"
                        },
                    }
                ).encode()
            )
            assert debugger.run_count == 0

            debugger.send(
                json.dumps(
                    {
                        "id": 2,
                        "method": "Runtime.runIfWaitingForDebugger",
                        "params": {},
                    }
                ).encode()
            )
            assert debugger.run_count == 1
            """
        )

        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_start_devtools_uses_chrome_compatible_loopback_host(self):
        """Chrome 130+ 的 DevTools CSP 要求 WebSocket 使用 127.0.0.1。"""

        with patch("pyv8.devtools.DevtoolsDebugger") as debugger_type, patch(
            "builtins.print"
        ) as print_mock:
            debugger = debugger_type.return_value
            devtools.start_devtools(object(), port=9010)

            debugger.start_server.assert_called_once_with("127.0.0.1", 9010)
            debugger.close.assert_called_once_with()
            output = "\n".join(call.args[0] for call in print_mock.call_args_list)
            self.assertIn("ws=127.0.0.1:9010", output)

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
