"""Browser-level tests for the pyv8c DevTools bridge."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from pyv8c import Browser
from pyv8c.core.errors import DOMException


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
