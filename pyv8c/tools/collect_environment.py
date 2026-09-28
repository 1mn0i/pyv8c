"""Read a real browser's environment into a profile ``BrowserEnvironment`` can load.

The collector starts the locally installed Chrome with a debugging port, reads every
key a browser reports from a local secure origin, and writes a profile.  Nothing is
downloaded and nothing is guessed: a key that cannot be read fails the run, so a
written profile is whole.  Headless runs report ``HeadlessChrome`` in the user agent;
use ``headed=True`` for the profile a normal visit presents.

Usage::

    python tools/collect_environment.py                     # chrome-<version>-<platform>.json
    python tools/collect_environment.py --out -             # to standard output
    python tools/collect_environment.py --headed --window-size 1920,1080
    python tools/collect_environment.py --accept-lang en-US,en
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..foundation.environment import COLLECTED_KEYS, BrowserEnvironment

#: The same variable the reference corpus reads, so one setting points both at Chrome.
CHROME_ENV = "PYV8C_CHROME"
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

#: One expression, so every value comes from the same turn of the same document.
COLLECT_SCRIPT = """
(async () => {
    const finite = value => Number.isFinite(value) ? value : String(value);
    const hints = navigator.userAgentData;
    const high = await hints.getHighEntropyValues(
        ['architecture', 'bitness', 'formFactors', 'fullVersionList', 'model',
         'platformVersion', 'uaFullVersion', 'wow64']);
    const battery = await navigator.getBattery();
    const estimate = await navigator.storage.estimate();
    const connection = navigator.connection;
    const brands = list => list.map(entry => ({brand: entry.brand, version: entry.version}));
    return JSON.stringify({
        'user_agent': navigator.userAgent,
        'app_version': navigator.appVersion,
        'vendor': navigator.vendor,
        'platform': navigator.platform,
        'language': navigator.language,
        'languages': Array.from(navigator.languages),
        'on_line': navigator.onLine,
        'hardware_concurrency': navigator.hardwareConcurrency,
        'device_memory': navigator.deviceMemory,
        'max_touch_points': navigator.maxTouchPoints,
        'webdriver': navigator.webdriver,
        'timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
        'viewport.width': innerWidth,
        'viewport.height': innerHeight,
        'viewport.device_scale_factor': devicePixelRatio,
        'screen.width': screen.width,
        'screen.height': screen.height,
        'screen.avail_width': screen.availWidth,
        'screen.avail_height': screen.availHeight,
        'screen.color_depth': screen.colorDepth,
        'screen.pixel_depth': screen.pixelDepth,
        'connection.effective_type': connection.effectiveType,
        'connection.rtt': connection.rtt,
        'connection.downlink': connection.downlink,
        'connection.save_data': connection.saveData,
        'battery.charging': battery.charging,
        'battery.charging_time': finite(battery.chargingTime),
        'battery.discharging_time': finite(battery.dischargingTime),
        'battery.level': battery.level,
        'storage.temporary.usage': estimate.usage,
        'storage.temporary.quota': estimate.quota,
        'client_hints.brands': brands(hints.brands),
        'client_hints.full_version_list': brands(high.fullVersionList),
        'client_hints.mobile': hints.mobile,
        'client_hints.platform': hints.platform,
        'client_hints.platform_version': high.platformVersion,
        'client_hints.architecture': high.architecture,
        'client_hints.bitness': high.bitness,
        'client_hints.wow64': high.wow64,
        'client_hints.model': high.model,
        'client_hints.form_factors': Array.from(high.formFactors),
        'plugins': Array.from(navigator.plugins).map(plugin => ({
            'name': plugin.name,
            'filename': plugin.filename,
            'description': plugin.description,
            'mime_types': Array.from(plugin).map(mime => ({
                'type': mime.type,
                'suffixes': mime.suffixes,
                'description': mime.description,
            })),
        })),
    });
})()
"""


class CollectionError(RuntimeError):
    """The environment could not be read, with the reason."""


def find_chrome(explicit=None):
    """The browser to read, or an error saying how to point at one."""
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise CollectionError(f"--chrome {explicit} does not exist")
        return path
    override = os.environ.get(CHROME_ENV)
    if override:
        path = Path(override)
        if not path.exists():
            raise CollectionError(f"{CHROME_ENV}={override} does not exist")
        return path
    for candidate in CHROME_PATHS:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return path
    raise CollectionError(
        f"no Chrome found in {', '.join(CHROME_PATHS)};"
        f" set {CHROME_ENV} to the browser to read"
    )


def read_devtools_port(profile_dir, *, timeout, poll=0.05):
    """``(port, browser_path)`` from the file Chrome writes once it is listening."""
    path = Path(profile_dir) / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while True:
        # Chrome writes the file as this reads it: half a file or a share violation means "not yet".
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        if len(lines) >= 2 and lines[0].strip().isdigit():
            return int(lines[0]), lines[1].strip()
        if time.monotonic() >= deadline:
            raise CollectionError(f"Chrome wrote no usable DevToolsActivePort within {timeout}s")
        time.sleep(poll)


def _http_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CdpSession:
    """A blocking DevTools connection: one request at a time, answers matched by id."""

    def __init__(self, url, *, timeout=30.0):
        from websockets.sync.client import connect

        self._timeout = timeout
        self._next_id = 0
        self._socket = connect(
            url, max_size=None, open_timeout=timeout, close_timeout=timeout, proxy=None
        )

    def call(self, method, **params):
        self._next_id += 1
        request_id = self._next_id
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(self._socket.recv(timeout=self._timeout))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CollectionError(f"{method} failed: {message['error'].get('message')}")
            return message.get("result", {})

    def evaluate(self, expression):
        result = self.call(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        details = result.get("exceptionDetails")
        if details:
            raised = details.get("exception", {}).get("description") or details.get("text")
            raise CollectionError(f"the page cannot answer: {raised}")
        return result["result"].get("value")

    def close(self):
        self._socket.close()


class _LocalPage(BaseHTTPRequestHandler):
    """One minimal document: localhost counts as trustworthy, so hints exist."""

    def do_GET(self):
        body = b"<!doctype html><html><head><title>environment</title></head><body></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


class _QuietServer(ThreadingHTTPServer):
    """A browser dropping its keep-alive connection is not an error to report."""

    def handle_error(self, request, client_address):
        pass


def _local_origin():
    """``(server, url)`` for a document on localhost."""
    server = _QuietServer(("127.0.0.1", 0), _LocalPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://localhost:{server.server_address[1]}/"


def _page_target(port, timeout):
    targets = _http_json(f"http://127.0.0.1:{port}/json/list", timeout)
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    return None


def _open_page(port, browser_url, timeout):
    browser = CdpSession(browser_url, timeout=timeout)
    try:
        browser.call("Target.createTarget", url="about:blank")
    finally:
        browser.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = _page_target(port, timeout)
        if url is not None:
            return url
        time.sleep(0.05)
    raise CollectionError("Chrome opened no page to read from")


def _session(port, timeout):
    """``(session, browser)`` for a page target, opening one when there is none."""
    version = _http_json(f"http://127.0.0.1:{port}/json/version", timeout)
    url = _page_target(port, timeout) or _open_page(port, version["webSocketDebuggerUrl"], timeout)
    return CdpSession(url, timeout=timeout), str(version.get("Browser", ""))


def _navigate(session, url, timeout):
    session.call("Page.navigate", url=url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if session.evaluate("document.readyState") == "complete":
                return
        except CollectionError:
            pass
        time.sleep(0.05)
    raise CollectionError(f"the local page {url} never finished loading")


def read_environment(chrome, *, headed=False, accept_lang=None, window_size=None,
                     extra_args=(), timeout=30.0):
    """``(payload, source)`` read from a browser started for this run."""
    with tempfile.TemporaryDirectory(prefix="pyv8c-chrome-") as profile:
        server, origin = _local_origin()
        arguments = [
            str(chrome),
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            # A non-browser client has to be allowed to attach to the socket.
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            # Instrumentation, not environment: without this the browser reports ``navigator.webdriver`` as true.
            "--disable-blink-features=AutomationControlled",
        ]
        if not headed:
            arguments.append("--headless=new")
        if accept_lang:
            arguments.append(f"--accept-lang={accept_lang}")
        if window_size:
            arguments.append(f"--window-size={window_size}")
        arguments.extend(str(argument) for argument in extra_args)

        process = subprocess.Popen(
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            port, _path = read_devtools_port(profile, timeout=min(timeout, 15.0))
            session, browser = _session(port, timeout)
            try:
                _navigate(session, origin, timeout)
                payload = json.loads(session.evaluate(COLLECT_SCRIPT))
            finally:
                session.close()
            source = {
                "browser": browser,
                "executable": str(chrome),
                "platform": sys.platform,
                "collected": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "arguments": [argument for argument in arguments[1:] if not argument.startswith("--user-data-dir")],
            }
            return payload, source
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            server.shutdown()
            server.server_close()


def build_environment(payload):
    """The payload as an environment, refusing anything but a whole profile."""
    if set(payload) != set(COLLECTED_KEYS):
        raise CollectionError(
            f"profile keys are not the collected ones:"
            f" missing {sorted(set(COLLECTED_KEYS) - set(payload))},"
            f" unknown {sorted(set(payload) - set(COLLECTED_KEYS))}"
        )
    return BrowserEnvironment(payload)


def default_name(source):
    browser, _, version = str(source.get("browser", "chrome/unknown")).partition("/")
    return f"{browser.lower() or 'chrome'}-{version or 'unknown'}-{source.get('platform', 'unknown')}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", help=f"browser executable; {CHROME_ENV} overrides the search")
    parser.add_argument("--out", help="where to write the profile, or - for standard output")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--accept-lang", help="pin the languages the browser reports")
    parser.add_argument("--window-size", help="pin the window size as WIDTH,HEIGHT")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the browser")
    parser.add_argument("--chrome-arg", action="append", default=[], help="extra browser argument")
    arguments = parser.parse_args(argv)

    try:
        chrome = find_chrome(arguments.chrome)
        payload, source = read_environment(
            chrome,
            headed=arguments.headed,
            accept_lang=arguments.accept_lang,
            window_size=arguments.window_size,
            extra_args=arguments.chrome_arg,
            timeout=arguments.timeout,
        )
        # Only the keys the browser answered; the rest stay defaults, unclaimed.
        text = build_environment(payload).dumps(source=source)
    except (CollectionError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if arguments.out == "-":
        sys.stdout.write(text)
        return 0
    path = Path(arguments.out or default_name(source))
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)
    return 0


__all__ = [
    "CHROME_ENV", "CHROME_PATHS", "COLLECT_SCRIPT", "CdpSession", "CollectionError",
    "build_environment", "default_name", "find_chrome", "main", "read_devtools_port",
    "read_environment",
]


if __name__ == "__main__":
    sys.exit(main())
