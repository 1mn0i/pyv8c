"""Calling-thread integration without any dependency on Browser."""

from concurrent.futures import Future
import json
import threading

import pytest
from websockets.sync.client import connect

from pyv8c import pyv8


class Window:
    pass


def debug_client(handle, *, pause=True, disconnect=False):
    result = Future()

    def run():
        try:
            with connect(f"ws://{handle.host}:{handle.port}/", close_timeout=1) as ws:
                for index, method in enumerate(["Runtime.enable", "Debugger.enable", "Runtime.runIfWaitingForDebugger"], 1):
                    ws.send(json.dumps({"id": index, "method": method}))
                    while json.loads(ws.recv(timeout=3)).get("id") != index:
                        pass
                if pause:
                    while True:
                        message = json.loads(ws.recv(timeout=3))
                        if message.get("method") == "Debugger.paused":
                            assert message["params"]["callFrames"][0]["location"]["lineNumber"] == 0
                            if disconnect:
                                break
                            ws.send(json.dumps({"id": 4, "method": "Debugger.resume"}))
                            while json.loads(ws.recv(timeout=3)).get("id") != 4:
                                pass
                            break
            result.set_result(True)
        except BaseException as error:
            result.set_exception(error)

    threading.Thread(target=run, daemon=True).start()
    return result


@pytest.mark.parametrize("disconnect", [False, True])
def test_run_keeps_callback_and_pause_on_calling_thread(disconnect):
    context = pyv8.Context(Window())
    ident = threading.get_ident()
    try:
        with pyv8.start_devtools(context, options=pyv8.DevToolsOptions(startup_timeout=3)) as handle:
            client = debug_client(handle, disconnect=disconnect)
            assert handle.wait_for_connection(2)

            def callback(ctx):
                assert ctx is context
                assert threading.get_ident() == ident
                return ctx.exec_js("globalThis.answer=42", filename="standalone.js")

            assert handle.run(callback) == 42
            assert client.result(3)
            assert context.exec_js("answer") == 42
        assert context.exec_js("21*2") == 42
        handle.close()
        assert not handle.wait_for_ready(0)
        assert not handle.wait_for_connection(0)
    finally:
        context.close()


def test_startup_timeout_does_not_call_callback():
    context = pyv8.Context(Window())
    try:
        with pyv8.start_devtools(context, options=pyv8.DevToolsOptions(startup_timeout=0)) as handle:
            calls = []
            with pytest.raises(TimeoutError, match="ready"):
                handle.run(lambda ctx: calls.append(ctx))
            assert calls == []
            assert context.exec_js("42") == 42
    finally:
        context.close()


def test_pause_disabled_and_context_first_close():
    context = pyv8.Context(Window())
    handle = pyv8.start_devtools(context, options=pyv8.DevToolsOptions(pause_on_start=False, startup_timeout=3))
    client = debug_client(handle, pause=False)
    assert handle.run(lambda ctx: ctx.exec_js("40+2")) == 42
    assert client.result(3)
    context.close()
    handle.close()
