"""Optional real-Chrome frontend smoke check (no third-party UI driver)."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

from websockets.sync.client import connect

from pyv8c import Browser


class Chrome:
    def __init__(self, executable, profile):
        startup = None
        if hasattr(subprocess, "STARTUPINFO"):
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        self.process = subprocess.Popen([
            executable, "--window-position=-32000,-32000", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-port=0", "--remote-allow-origins=*",
            "--user-data-dir=" + profile, "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        port_file = Path(profile) / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not port_file.exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Chrome did not open its debugging endpoint")
            time.sleep(0.05)
        self.port = int(port_file.read_text().splitlines()[0])
        with urlopen(f"http://127.0.0.1:{self.port}/json/version") as response:
            version = json.load(response)
        self.version = version["Browser"]
        self.ws = connect(version["webSocketDebuggerUrl"], close_timeout=1)
        self.sequence = 0

    def request(self, method, params=None, session=None):
        self.sequence += 1
        payload = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        self.ws.send(json.dumps(payload))
        while True:
            result = json.loads(self.ws.recv(timeout=10))
            if result.get("id") == self.sequence:
                if "error" in result:
                    raise RuntimeError(result)
                return result.get("result", {})

    def open_frontend(self, url):
        url = url.replace("devtools://devtools/bundled/", f"http://127.0.0.1:{self.port}/devtools/")
        target = self.request("Target.createTarget", {"url": url})["targetId"]
        session = self.request("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        return target, session

    def evaluate(self, session, expression):
        result = self.request("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        }, session)
        if "exceptionDetails" in result:
            raise RuntimeError(result)
        return result["result"].get("value")

    def models(self, session):
        return self.evaluate(session, """(async () => {
            const SDK = await import('./core/sdk/sdk.js');
            return SDK.TargetManager.TargetManager.instance().models(SDK.DebuggerModel.DebuggerModel)
                .map(model => ({id:model.target().id(), type:model.target().type(),
                    paused:model.isPaused(), url:model.debuggerPausedDetails()?.callFrames[0]?.location()?.scriptId}));
        })()""")

    def wait_paused(self, session, kind):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            models = self.models(session)
            if any(model["paused"] and model["type"] == kind for model in models):
                return models
            time.sleep(0.05)
        raise AssertionError(("Chrome frontend did not show pause", models))

    def resume_all(self, session):
        self.evaluate(session, """(async () => {
            const SDK = await import('./core/sdk/sdk.js');
            for (const model of SDK.TargetManager.TargetManager.instance().models(SDK.DebuggerModel.DebuggerModel)) {
                if (model.isPaused()) await model.resume();
            }
        })()""")

    def close(self):
        try:
            self.request("Browser.close")
        finally:
            self.ws.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    args = parser.parse_args()
    browser = Browser(stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page)
    methods = []
    dispatch = handle._router.dispatch

    def observed(raw, generation):
        methods.append(json.loads(raw).get("method"))
        return dispatch(raw, generation)

    handle._router.dispatch = observed
    profile = tempfile.TemporaryDirectory(prefix="pyv8-devtools-chrome-")
    chrome = None
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        chrome = Chrome(args.chrome, profile.name)
        with urlopen(f"http://{handle.host}:{handle.port}/json/list") as response:
            targets = json.load(response)
        assert targets[0]["devtoolsFrontendUrl"] == handle.url
        target, session = chrome.open_frontend(targets[0]["devtoolsFrontendUrl"])
        if not handle.wait_for_ready(15):
            raise AssertionError(("Chrome handshake", methods, chrome.evaluate(session, "({url:location.href,text:document.body.innerText})")))
        result = pool.submit(page.evaluate, "globalThis.debugValue=42", filename="example.js")
        print("Chrome", chrome.version, "first statement", chrome.wait_paused(session, "frame"), flush=True)
        chrome.resume_all(session)
        assert result.result(5) == 42
        page.evaluate("globalThis.worker = new Worker(URL.createObjectURL(new Blob(['globalThis.workerValue=42'])))")
        print("Worker", chrome.wait_paused(session, "worker"), flush=True)
        chrome.resume_all(session)
        chrome.request("Target.closeTarget", {"targetId": target})
        target, session = chrome.open_frontend(handle.url)
        deadline = time.monotonic() + 15
        while not handle.wait_for_ready(0.1):
            assert time.monotonic() < deadline, "Chrome did not reconnect"
            time.sleep(0.05)
        assert page.evaluate("debugValue") == 42
        print("Reconnect", chrome.models(session), flush=True)
        print("PASS: real Chrome discovery/frontend handshake, first statement, worker, reconnect", flush=True)
    finally:
        if chrome is not None:
            chrome.close()
        handle.close()
        browser.close()
        pool.shutdown(wait=True)
        profile.cleanup()


if __name__ == "__main__":
    main()
