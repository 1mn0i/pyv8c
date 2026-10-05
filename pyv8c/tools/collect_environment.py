"""Read a real browser's environment and window surface into one profile.

The collector starts the locally installed Chrome with a debugging port, reads every
key a browser reports from a local secure origin, and reads what that browser leaves out
of ``window`` and of each interface's prototype from the same session.  Nothing is
downloaded and nothing is guessed: a key that cannot be read fails the run, so a written
profile is whole.  Headless runs report ``HeadlessChrome`` in the user agent; use
``headed=True`` for the profile a normal visit presents.

Usage::

    python tools/collect_environment.py                     # writes the packaged profile
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

from ..engine.baseline import BaselineRepository, InterfaceTable
from ..foundation.environment import COLLECTED_KEYS, SCHEMA_VERSION, BrowserEnvironment, json_value

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
#: Where a run writes unless told otherwise.
DEFAULT_OUTPUT = PACKAGE_ROOT / "data" / "chrome-environment.json"

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
    const gl = document.createElement('canvas').getContext('webgl');
    const rendererInfo = gl === null ? null : gl.getExtension('WEBGL_debug_renderer_info');
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
        'webgl.vendor': rendererInfo ? gl.getParameter(rendererInfo.UNMASKED_VENDOR_WEBGL) : '',
        'webgl.renderer': rendererInfo ? gl.getParameter(rendererInfo.UNMASKED_RENDERER_WEBGL) : '',
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


#: What a page enumerates: the window's own names, and each interface's prototype names.
#: The window alone cannot answer whether ``Element.prototype`` carries ``computedRole``.
SURFACE_SCRIPT = """
(() => {
    const prototypes = {};
    const read = (name, prototype) => {
        if (!name || !prototype || typeof prototype !== 'object' || name in prototypes) return;
        try { prototypes[name] = Object.getOwnPropertyNames(prototype).sort(); } catch (error) {}
    };
    for (const name of Object.getOwnPropertyNames(window)) {
        let value = null;
        try { value = window[name]; } catch (error) { value = null; }
        if (typeof value === 'function') {
            try { read(name, value.prototype); } catch (error) {}
        }
    }
    // Interfaces without a global constructor are read off the objects that carry one.
    const makers = [() => document, () => document.body, () => document.createElement('div'),
                    () => document.createElement('canvas'), () => document.createElement('iframe'),
                    () => document.createElement('video'), () => document.createTextNode(''),
                    () => document.createComment(''), () => new Image(),
                    () => new CSSStyleSheet()];
    for (const make of makers) {
        try {
            const prototype = Object.getPrototypeOf(make());
            read(prototype.constructor.name, prototype);
        } catch (error) {}
    }
    return JSON.stringify({
        keys: Object.keys(window).sort(),
        own: Object.getOwnPropertyNames(window).sort(),
        prototypes});
})()"""


class CollectionError(RuntimeError):
    """The browser could not be read, with the reason."""


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


def read_pages(chrome, scripts, *, headed=False, accept_lang=None, window_size=None,
               extra_args=(), timeout=30.0):
    """``(values, source)`` for several expressions read from one browser session.

    Every answer comes from the same page, so facts read together describe one browser.
    """
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
                values = [session.evaluate(script) for script in scripts]
            finally:
                session.close()
            source = {
                "browser": browser,
                "executable": str(chrome),
                "platform": sys.platform,
                "collected": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "arguments": [argument for argument in arguments[1:] if not argument.startswith("--user-data-dir")],
            }
            return values, source
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            server.shutdown()
            server.server_close()


def baseline_view(snapshot=None):
    """``(declared prototype members, digest)`` of the baseline a run measures against.

    Reading it needs no native extension, so a collection runs without pyv8.
    """
    snapshot = snapshot if snapshot is not None else BaselineRepository.load()
    table = InterfaceTable(snapshot)
    declared = {name: {member.name for member in table.interface(name).own_members("prototype")}
                for name in table.names()}
    return declared, str(getattr(snapshot, "digest", "") or "")


def read_profile(chrome, view=None, **options):
    """``(payload, surface, source)``: both readings a run makes, from one session."""
    declared, digest = view if view is not None else baseline_view()
    values, source = read_pages(chrome, (COLLECT_SCRIPT, SURFACE_SCRIPT), **options)
    return json.loads(values[0]), build_surface(json.loads(values[1]), declared, digest), source


def build_environment(payload):
    """The payload as an environment, refusing anything but a whole profile."""
    if set(payload) != set(COLLECTED_KEYS):
        raise CollectionError(
            f"profile keys are not the collected ones:"
            f" missing {sorted(set(COLLECTED_KEYS) - set(payload))},"
            f" unknown {sorted(set(payload) - set(COLLECTED_KEYS))}"
        )
    return BrowserEnvironment(payload)


def build_surface(payload, declared=None, baseline_digest=""):
    """What the browser does not install, as the profile's surface section.

    The window names cover the globals; the prototype gaps are measured against
    ``declared`` (``{interface: {prototype names}}``), and ``baseline_digest`` records
    which baseline those names came from.
    """
    declared = declared or {}
    gaps = {}
    for name, names in (payload.get("prototypes") or {}).items():
        known = declared.get(str(name))
        if not known or not isinstance(names, list):
            continue
        missing = sorted(known - {str(entry) for entry in names})
        if missing:
            gaps[str(name)] = missing
    return {"keys": list(payload["keys"]), "own_names": list(payload["own"]),
            "prototype_gaps": {name: gaps[name] for name in sorted(gaps)},
            "baseline_digest": str(baseline_digest or "")}


def build_profile(payload, surface, source):
    """The document a run writes: the environment keys, and the surface they came with.

    A non-finite number travels as its name, so a strict JSON parser accepts the file.
    """
    document = {"schema": SCHEMA_VERSION, "source": dict(source)}
    document.update(build_environment(payload).to_dict())
    document["window_surface"] = surface
    return json_value(document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", help=f"browser executable; {CHROME_ENV} overrides the search")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                        help="where to write the profile, or - for standard output (default: %(default)s)")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--accept-lang", help="pin the languages the browser reports")
    parser.add_argument("--window-size", help="pin the window size as WIDTH,HEIGHT")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the browser")
    parser.add_argument("--chrome-arg", action="append", default=[], help="extra browser argument")
    arguments = parser.parse_args(argv)

    try:
        chrome = find_chrome(arguments.chrome)
        payload, surface, source = read_profile(
            chrome,
            headed=arguments.headed,
            accept_lang=arguments.accept_lang,
            window_size=arguments.window_size,
            extra_args=arguments.chrome_arg,
            timeout=arguments.timeout,
        )
        # Only the keys the browser answered; the rest stay defaults, unclaimed.
        document = build_profile(payload, surface, source)
    except (CollectionError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    text = json.dumps(document, indent=2) + "\n"
    if str(arguments.out) == "-":
        sys.stdout.write(text)
        return 0
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(text, encoding="utf-8")
    print(
        f"wrote {arguments.out}: {len(surface['own_names'])} own names,"
        f" {len(surface['prototype_gaps'])} interfaces with prototype gaps,"
        f" {len(surface['keys'])} keys, from {source.get('browser', 'unknown')}",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "CHROME_ENV", "CHROME_PATHS", "COLLECT_SCRIPT", "DEFAULT_OUTPUT", "SURFACE_SCRIPT",
    "CdpSession", "CollectionError", "baseline_view", "build_environment", "build_profile",
    "build_surface", "find_chrome", "main", "read_devtools_port", "read_pages", "read_profile",
]


if __name__ == "__main__":
    sys.exit(main())
