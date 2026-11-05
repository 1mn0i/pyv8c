"""Scan every pause for a long string in the frame's local/closure scopes."""

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROXY = "http://127.0.0.1:7890"
OUT = Path(tempfile.gettempdir()) / "otto_chrome_boundary_sample.json"


async def main():
    async with async_playwright() as playwright:
        process = subprocess.Popen(
            [CHROME, "--remote-debugging-port=19399",
             "--user-data-dir=" + tempfile.mkdtemp(prefix="otto_scan_"),
             # A remote-debugging port alone makes navigator.webdriver true; the baseline
             # must match a real browser (and pyv8c, which reports false).
             "--disable-blink-features=AutomationControlled",
             "--proxy-server=" + PROXY, "--no-first-run", "--start-minimized", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = None
        for _ in range(60):
            try:
                browser = await playwright.chromium.connect_over_cdp("http://127.0.0.1:19399")
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.25)
        if browser is None:
            process.terminate()
            return

        page = await browser.contexts[0].new_page()
        session = await page.context.new_cdp_session(page)
        await session.send("Page.enable")
        await session.send("Debugger.enable")
        await session.send("Runtime.enable")

        frames = set()
        captures = []
        done = asyncio.Event()
        stats = {"armed": 0, "pauses": 0}
        tasks = []

        session.on("Page.frameNavigated",
                   lambda event: frames.add(event["frame"]["id"])
                   if "/fp" in event.get("frame", {}).get("url", "") else None)

        async def arm(event):
            context = event["context"]
            aux = context.get("auxData", {})
            if aux.get("frameId") not in frames or not aux.get("isDefault"):
                return
            try:
                value = await session.send("Runtime.evaluate", {
                    "expression": "TextEncoder.prototype.encode",
                    "contextId": context["id"], "objectGroup": "boundary"})
                await session.send("Debugger.setBreakpointOnFunctionCall",
                                   {"objectId": value["result"]["objectId"]})
                stats["armed"] += 1
                print("ARMED", context["id"], flush=True)
            except Exception as error:  # noqa: BLE001
                print("ARM_ERROR", type(error).__name__, str(error)[:50], flush=True)

        session.on("Runtime.executionContextCreated",
                   lambda event: tasks.append(asyncio.create_task(arm(event))))

        async def scan_scope(scope):
            own = await session.send("Runtime.getProperties", {
                "objectId": scope["object"]["objectId"], "ownProperties": True})
            shapes = []
            for prop in own.get("result", []):
                value = prop.get("value", {})
                if value.get("type") == "string":
                    length = len(value.get("value") or "")
                    shapes.append("%s=string(%d)" % (prop.get("name"), length))
                    if value["value"].startswith('["2:') and 10000 < length < 60000:
                        captures.append(value["value"])
                        print("CAPTURE_STR", prop.get("name"), length, flush=True)
                elif value.get("subtype") == "array":
                    shapes.append("%s=%s" % (prop.get("name"), (value.get("description") or "")[:20]))
                    try:
                        child = await session.send("Runtime.getProperties", {
                            "objectId": value["objectId"], "ownProperties": True})
                    except Exception:  # noqa: BLE001
                        continue
                    for item in child.get("result", []):
                        raw = item.get("value", {}).get("value")
                        if isinstance(raw, str) and raw.startswith('["2:') and 10000 < len(raw) < 60000:
                            captures.append(raw)
                            print("CAPTURE_ARR", prop.get("name"), item.get("name"),
                                  len(raw), flush=True)
                else:
                    shapes.append("%s=%s" % (prop.get("name"), value.get("type")))
            return shapes

        async def on_pause(event):
            stats["pauses"] += 1
            try:
                frame = event["callFrames"][0]
                summaries = []
                for scope in frame.get("scopeChain", []):
                    if scope.get("type") not in ("local", "closure"):
                        continue
                    summaries.append((scope.get("type"), await scan_scope(scope)))
                print("P", stats["pauses"], frame.get("functionName"), summaries[:2], flush=True)
            except Exception as error:  # noqa: BLE001
                print("SCAN_ERROR", type(error).__name__, str(error)[:50], flush=True)
            finally:
                try:
                    await session.send("Debugger.resume")
                except Exception:  # noqa: BLE001
                    pass

        session.on("Debugger.paused", lambda event: tasks.append(asyncio.create_task(on_pause(event))))

        async def collect(request):
            try:
                response = await request.response()
                body = await response.text() if response else ""
                print("TL", response.status if response else None, repr(body), flush=True)
            except Exception:  # noqa: BLE001
                pass
            finally:
                done.set()

        page.on("request", lambda request: tasks.append(asyncio.create_task(collect(request)))
                if request.method == "POST" and request.url.split("?")[0].endswith("/tl") else None)

        try:
            await page.goto("https://www.otto.de/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.wait_for(done.wait(), 45)
        except Exception as error:  # noqa: BLE001
            print("NAV", type(error).__name__, str(error)[:80], flush=True)

        print("STATS", stats, "captures", len(captures), flush=True)
        if captures:
            OUT.write_text(captures[-1], encoding="utf8")
            print("SAVED", len(json.loads(captures[-1])), flush=True)

        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            await session.detach()
        except Exception:  # noqa: BLE001
            pass
        await browser.close()
        process.terminate()


asyncio.run(main())
