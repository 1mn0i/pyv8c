"""Collect Chrome's ordered host-call sequence with a background CDP reader."""

import asyncio
import json
import subprocess
import tempfile
import urllib.request

import websockets

CHROME = r"D:/src/pyv8env/chrome-win64/chrome.exe"
PROXY = "http://127.0.0.1:7890"
PORT = 19469

HOOK = r"""
(function(){
  if (window.__seqInstalled) { return; }
  window.__seqInstalled = true;
  window.__seq = [];
  try { window.__seqLabel = String(location.pathname || ''); } catch (e) { window.__seqLabel = '?'; }
  var push = function (label) { try { if (window.__seq.length < 4000) { window.__seq.push(label); } } catch (e) {} };
  var wrap = function (owner, method, label) {
    try {
      if (!owner || typeof owner[method] !== 'function') { return; }
      var original = owner[method];
      owner[method] = function () { push(label); return original.apply(this, arguments); };
    } catch (e) {}
  };
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'fillText', 'fillText');
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'measureText', 'measureText');
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'getImageData', 'getImageData');
  wrap(window.HTMLCanvasElement && HTMLCanvasElement.prototype, 'toDataURL', 'toDataURL');
  wrap(window.HTMLCanvasElement && HTMLCanvasElement.prototype, 'getContext', 'getContext');
  wrap(window.WebGLRenderingContext && WebGLRenderingContext.prototype, 'getParameter', 'getParameter');
  wrap(window.WebGLRenderingContext && WebGLRenderingContext.prototype, 'getExtension', 'getExtension');
  wrap(window.crypto, 'getRandomValues', 'getRandomValues');
  wrap(window.navigator && navigator.permissions, 'query', 'permissions.query');
  wrap(window.navigator && navigator.mediaDevices, 'enumerateDevices', 'enumerateDevices');
  wrap(window.navigator, 'getBattery', 'getBattery');
  wrap(window.navigator && navigator.storage, 'estimate', 'storage.estimate');
})();
"""


async def main():
    process = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}",
         "--user-data-dir=" + tempfile.mkdtemp(prefix="otto_seq3_"),
         "--disable-blink-features=AutomationControlled",
         "--proxy-server=" + PROXY, "--no-first-run", "--start-minimized", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        for _ in range(80):
            await asyncio.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as response:
                    listing = json.loads(response.read().decode("utf8"))
                pages = [item for item in listing if item.get("type") == "page"]
                if pages:
                    target = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                continue
        if target is None:
            print("NO_TARGET")
            return

        async with websockets.connect(target, max_size=2 ** 25) as ws:
            counter = {"id": 0}
            waiting = {}
            contexts = []

            async def reader():
                while True:
                    raw = await ws.recv()
                    message = json.loads(raw)
                    method = message.get("method")
                    if method == "Runtime.executionContextCreated":
                        context = message["params"]["context"]
                        contexts.append((context["id"], context.get("auxData", {}).get("isDefault")))
                        continue
                    message_id = message.get("id")
                    if message_id in waiting:
                        waiting[message_id].set_result(message)
                        waiting.pop(message_id, None)

            async def call(method, params=None, timeout=45):
                counter["id"] += 1
                message_id = counter["id"]
                future = asyncio.get_running_loop().create_future()
                waiting[message_id] = future
                await ws.send(json.dumps({"id": message_id, "method": method,
                                          "params": params or {}}))
                return await asyncio.wait_for(future, timeout=timeout)

            reader_task = asyncio.create_task(reader())
            await call("Page.enable")
            await call("Runtime.enable")
            await call("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
            await call("Page.navigate", {"url": "https://www.otto.de/"})

            collected = {}
            best = 0
            for _round in range(12):
                await asyncio.sleep(3.0)
                for context_id, _is_default in list(contexts):
                    result = await call("Runtime.evaluate",
                                        {"expression": "JSON.stringify({label: window.__seqLabel,"
                                                       " seq: (window.__seq || []).slice(0, 2000)})",
                                         "contextId": context_id, "returnByValue": True},
                                        timeout=25)
                    value = ((result.get("result") or {}).get("result") or {}).get("value")
                    if not value:
                        continue
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        continue
                    sequence = parsed.get("seq") or []
                    if sequence:
                        key = parsed.get("label", str(context_id)) + "#" + str(context_id)
                        collected[key] = sequence
                        best = max(best, max((len(seq) for seq in collected.values()), default=0))
                if best > 400:
                    break

            reader_task.cancel()
            print("CONTEXTS", len(contexts), "COLLECTED", len(collected), "BEST", best)
            for label, sequence in collected.items():
                print("SEQ", label, len(sequence), json.dumps(sequence[:30], ensure_ascii=False))
            with open(r"C:/Users/huangconghua/AppData/Local/Temp/chrome_seq.json",
                      "w", encoding="utf8") as handle:
                json.dump(collected, handle, ensure_ascii=False)
    finally:
        process.terminate()


asyncio.run(main())
