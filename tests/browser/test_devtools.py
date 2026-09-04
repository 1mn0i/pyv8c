"""Browser-level tests for the pyv8c DevTools bridge."""

import asyncio
import json
import threading
import time

import pytest
from websockets.asyncio.client import connect

from pyv8c import Browser
from pyv8c.foundation.errors import DOMException


async def _request(ws, payload):
    await ws.send(json.dumps(payload))
    while True:
        result = json.loads(await asyncio.wait_for(ws.recv(), 2))
        if result.get("id") == payload.get("id"):
            return result


@pytest.fixture
def devtools_page():
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<html><body><script>globalThis.answer = 40;</script></body></html>")
    handle = context.start_devtools(page)
    try:
        yield context, page, handle
    finally:
        handle.close()
        browser.close()


def test_start_is_non_blocking_and_exposes_target(devtools_page):
    context, page, handle = devtools_page
    assert handle.host == "127.0.0.1"
    assert handle.port > 0
    assert handle.url.endswith(f"ws=127.0.0.1:{handle.port}")
    assert page.evaluate("answer") == 40

    async def check():
        async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
            result = await _request(ws, {"id": 1, "method": "Target.getTargets"})
            assert len(result["result"]["targetInfos"]) == 1
            target = result["result"]["targetInfos"][0]
            assert target["type"] == "page"
            evaluation = await _request(ws, {
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {"expression": "answer + 2"},
            })
            assert evaluation["result"]["result"]["value"] == 42

    asyncio.run(check())


def test_target_attach_and_detach(devtools_page):
    _, _, handle = devtools_page

    async def check():
        async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
            targets = await _request(ws, {"id": 1, "method": "Target.getTargets"})
            target_id = targets["result"]["targetInfos"][0]["targetId"]
            attached = await _request(ws, {
                "id": 2, "method": "Target.attachToTarget",
                "params": {"targetId": target_id},
            })
            session_id = attached["result"]["sessionId"]
            event = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert event["method"] == "Target.attachedToTarget"
            assert event["params"]["sessionId"] == session_id
            detached = await _request(ws, {
                "id": 3, "method": "Target.detachFromTarget",
                "params": {"sessionId": session_id},
            })
            assert detached["result"] == {}

    asyncio.run(check())


def test_only_one_devtools_handle_per_context(devtools_page):
    context, page, _ = devtools_page
    with pytest.raises(DOMException):
        context.start_devtools(page)


def test_page_close_removes_target(devtools_page):
    _, page, handle = devtools_page

    async def check():
        async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
            await asyncio.sleep(0.05)
            page.close()
            await asyncio.sleep(0.05)
            event = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert event["method"] == "Target.targetDestroyed"

    asyncio.run(check())


def test_start_paused_waits_before_first_script_and_resumes():
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, start_paused=True, startup_timeout=3)
    completed = threading.Event()
    errors = []

    def navigate():
        try:
            page.set_content("<script>globalThis.answer = 42;</script>")
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=navigate)
    thread.start()
    try:
        time.sleep(0.05)
        assert not completed.is_set()

        async def resume():
            async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
                await ws.send(json.dumps({"id": 1, "method": "Runtime.runIfWaitingForDebugger"}))

        asyncio.run(resume())
        thread.join(2)
        assert completed.is_set()
        assert errors == []
        assert page.evaluate("answer") == 42
    finally:
        handle.close()
        browser.close()


def test_run_if_waiting_command_releases_gate_before_socket_closes():
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, start_paused=True, startup_timeout=3)
    completed = threading.Event()

    def navigate():
        page.set_content("<script>globalThis.answer = 42;</script>")
        completed.set()

    thread = threading.Thread(target=navigate)
    thread.start()
    try:
        time.sleep(0.05)
        assert not completed.is_set()

        async def resume_and_wait():
            async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
                await ws.send(json.dumps({"id": 7, "method": "Runtime.runIfWaitingForDebugger"}))
                response = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert response["id"] == 7
                assert "error" not in response

        asyncio.run(resume_and_wait())
        thread.join(2)
        assert completed.is_set()
        assert page.evaluate("answer") == 42
    finally:
        handle.close()
        browser.close()


def test_debugger_pause_loop_routes_resume():
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, start_paused=True, startup_timeout=3)
    completed = threading.Event()

    def navigate():
        page.set_content(
            "<script>globalThis.answer = 1; debugger; globalThis.answer = 2;</script>"
        )
        completed.set()

    thread = threading.Thread(target=navigate)
    thread.start()
    try:
        async def debug():
            async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
                await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
                targets = json.loads(await ws.recv())["result"]["targetInfos"]
                target_id = targets[0]["targetId"]
                await ws.send(json.dumps({
                    "id": 2, "method": "Target.attachToTarget",
                    "params": {"targetId": target_id},
                }))
                await ws.recv()
                attached = json.loads(await ws.recv())
                session_id = attached["params"]["sessionId"]
                for ident, method in ((3, "Runtime.enable"), (4, "Debugger.enable"),
                                      (5, "Runtime.runIfWaitingForDebugger")):
                    await ws.send(json.dumps({
                        "id": ident, "method": method, "sessionId": session_id,
                    }))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    if message.get("method") == "Debugger.paused":
                        await ws.send(json.dumps({
                            "id": 6, "method": "Debugger.resume", "sessionId": session_id,
                        }))
                        return

        asyncio.run(debug())
        thread.join(2)
        assert completed.is_set()
        assert page.evaluate("answer") == 2
    finally:
        handle.close()
        browser.close()


def test_devtools_resume_can_be_received_while_runtime_evaluate_is_paused(devtools_page):
    """The WebSocket reader must remain live while V8 is in its pause loop."""
    _, page, handle = devtools_page

    async def check():
        async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
            targets = await _request(ws, {"id": 1, "method": "Target.getTargets"})
            target_id = targets["result"]["targetInfos"][0]["targetId"]
            await ws.send(json.dumps({"id": 2, "method": "Target.attachToTarget",
                                       "params": {"targetId": target_id}}))
            attached_response = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert attached_response["id"] == 2
            attached_event = json.loads(await asyncio.wait_for(ws.recv(), 2))
            session_id = attached_event["params"]["sessionId"]
            await ws.send(json.dumps({"id": 3, "method": "Runtime.enable",
                                       "sessionId": session_id}))
            await ws.send(json.dumps({"id": 4, "method": "Debugger.enable",
                                       "sessionId": session_id}))
            # Do not wait for the evaluate response before reading again: it
            # pauses at `debugger` and requires a second command to progress.
            await ws.send(json.dumps({"id": 5, "method": "Runtime.evaluate",
                                       "sessionId": session_id,
                                       "params": {"expression": "debugger; 7"}}))
            paused = None
            while paused is None:
                message = json.loads(await asyncio.wait_for(ws.recv(), 2))
                if message.get("method") == "Debugger.paused":
                    paused = message
            await ws.send(json.dumps({"id": 6, "method": "Debugger.resume",
                                       "sessionId": session_id}))
            while True:
                response = json.loads(await asyncio.wait_for(ws.recv(), 2))
                if response.get("id") == 6:
                    assert "error" not in response
                if response.get("id") == 5:
                    assert response["result"]["result"]["value"] == 7
                    return

    asyncio.run(check())


def test_start_paused_timeout_releases_navigation():
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, start_paused=True, startup_timeout=0.05)
    errors = []

    def navigate():
        try:
            page.set_content("<script>globalThis.answer = 1;</script>")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=navigate)
    thread.start()
    try:
        thread.join(2)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert "timed out" in str(errors[0])
    finally:
        handle.close()
        browser.close()
