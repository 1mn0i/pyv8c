"""``page.net``: host requests over the context's transport.

These run against a real local server with the real curl_cffi engine, because
what they are about is exactly what a fake would hide: that a host request and a
page request are the same session as far as the server is concerned -- one cookie
jar, the browser's headers, and the same transport underneath both.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytest.importorskip("curl_cffi", reason="these exercise the curl_cffi engine")

from pyv8c import Browser
from pyv8c.foundation.host_errors import LoadFailure


class _EchoHandler(BaseHTTPRequestHandler):
    """A tiny server: sets cookies, and reports back what it was sent."""

    def _reply(self, body, content_type="application/json", cookies=()):
        self.send_response(200)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Server", "echo")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, cookies=()):
        self._reply(json.dumps(payload).encode("utf-8"), cookies=cookies)

    def do_GET(self):
        if self.path.startswith("/set"):
            self._json({"set": True}, cookies=["a=1; Path=/", "b=2; Path=/"])
            return
        if self.path.startswith("/echo"):
            self._json({
                "cookie": self.headers.get("Cookie"),
                "ua": self.headers.get("User-Agent"),
                "language": self.headers.get("Accept-Language"),
                "custom": self.headers.get("X-Custom"),
                "referer": self.headers.get("Referer"),
                "accept": self.headers.get("Accept"),
                "encoding": self.headers.get("Accept-Encoding"),
                "destination": self.headers.get("Sec-Fetch-Dest"),
                "mode": self.headers.get("Sec-Fetch-Mode"),
                "site": self.headers.get("Sec-Fetch-Site"),
                "hints": self.headers.get("Sec-CH-UA"),
            })
            return
        self._reply(b"<html><body>page</body></html>", content_type="text/html; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        self._json({"type": self.headers.get("Content-Type"), "body": body})

    def log_message(self, _format, *_args):
        pass


@pytest.fixture
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def page(origin):
    browser = Browser(
        stub_logging="off",
        http_engine="curl_cffi",
        default_headers={"X-Custom": "browser"},
    )
    instance = browser.new_profile().new_page(origin + "/")
    try:
        yield instance
    finally:
        browser.close()


def test_page_net_sends_the_environment_headers_and_the_jar(page, origin):
    page.net.get("/set")
    payload = page.net.get("/echo").json()

    assert payload["ua"].startswith("Mozilla/5.0")
    assert payload["language"] == "zh-CN,zh;q=0.9"
    assert payload["custom"] == "browser", "Browser(default_headers=...) applies"
    assert payload["cookie"] == "a=1; b=2", "both cookies the response set are sent back"
    # A host request now carries what the page's own fetch would carry.
    assert payload["accept"] == "*/*"
    assert payload["encoding"] == "gzip, deflate, br, zstd"
    assert payload["destination"] == "empty"
    assert payload["mode"] == "cors"
    assert payload["site"] == "same-origin"
    assert payload["hints"] == '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"'


def test_headers_override_in_three_layers(page):
    page.net.headers["X-Custom"] = "page"
    assert page.net.get("/echo").json()["custom"] == "page"
    assert page.net.get("/echo", headers={"X-Custom": "call"}).json()["custom"] == "call"
    # A caller's own spelling of a default header wins over the derived one.
    assert page.net.get("/echo", headers={"user-agent": "custom-agent"}).json()["ua"] == "custom-agent"


def test_page_net_resolves_relative_urls_and_query_parameters(page, origin):
    response = page.net.get("/echo", params={"q": "1", "r": "2"})
    assert response.url == f"{origin}/echo?q=1&r=2"
    assert response.ok is True
    assert response.headers["x-server"] == "echo"


def test_page_net_posts_json_and_answers_asynchronously(page):
    posted = page.net.post("/echo", json={"a": 1})
    assert posted.json()["type"] == "application/json"
    assert json.loads(posted.json()["body"]) == {"a": 1}

    future = page.net.get_async("/echo")
    assert future.result(timeout=10).status == 200


def test_page_net_can_drop_or_override_cookies(page):
    page.net.get("/set")
    assert page.net.get("/echo", cookies=False).json()["cookie"] is None
    overridden = page.net.get("/echo", cookies={"a": "override"}).json()["cookie"]
    assert overridden == "a=override; b=2"


def test_cookies_are_shared_with_the_page_in_both_directions(page):
    # A host request's cookies are visible to the document...
    page.net.get("/set")
    assert page.evaluate("document.cookie") == "a=1; b=2"

    # ...a page fetch sends them through the same transport...
    page.evaluate("fetch('/echo').then(r => r.json()).then(v => window.__fromPage = v)")
    page.run_until_idle(timeout=5)
    assert page.evaluate("__fromPage")["cookie"] == "a=1; b=2"

    # ...and a cookie a page script sets is sent by the next host request.
    page.evaluate("document.cookie = 'c=3; path=/'")
    assert page.net.get("/echo").json()["cookie"] == "a=1; b=2; c=3"


def test_the_host_api_stays_out_of_the_realm():
    """Nothing of ``page.net`` is visible to page code.

    A browser has no ``net`` global, so neither may this host API appear as one:
    a page that goes looking must find it undefined, along with the bridge global
    that used to carry the extension's native function.
    """
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate("[typeof window.net, typeof globalThis.net]") == ["undefined", "undefined"]
        assert page.evaluate("typeof globalThis.__pyv8cNetHost__") == "undefined"
    finally:
        browser.close()


def test_page_net_without_a_transport_says_so():
    browser = Browser(stub_logging="off", real_network=False)
    try:
        instance = browser.new_profile().new_page()
        # A missing engine is the host's own failure to load, so the host sees it.
        with pytest.raises(LoadFailure) as failure:
            instance.net.get("http://example.test/")
        assert instance.net.available is False
    finally:
        browser.close()
    assert "real_network" in str(failure.value)


def test_host_request_future_tracks_running_and_queued_cancellation(monkeypatch):
    """Cancelling queued work prevents the request; running work cannot be cancelled."""
    import threading
    from concurrent.futures import CancelledError
    from types import SimpleNamespace
    from pyv8c.platform.net import PageNet

    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    calls = []

    def request(value):
        calls.append(value)
        entered[value].set()
        assert release.wait(3)
        return value

    net = PageNet(SimpleNamespace(profile=SimpleNamespace(browser=SimpleNamespace())))
    monkeypatch.setattr(net, "_backend", lambda: SimpleNamespace(request_sync=request))
    monkeypatch.setattr(net, "build", lambda method, url, **kw: url)
    try:
        first = net.request_async("GET", 0)
        second = net.request_async("GET", 1)
        assert all(event.wait(3) for event in entered)
        assert first.running() and not first.cancel()
        queued = net.request_async("GET", 2)
        assert queued.cancel()
        release.set()
        assert first.result(3) == 0 and second.result(3) == 1
        with pytest.raises(CancelledError):
            queued.result(3)
        assert sorted(calls) == [0, 1]
    finally:
        release.set()
        net.close()


def test_host_request_shutdown_completes_cancelled_queue(monkeypatch):
    import threading
    from types import SimpleNamespace
    from pyv8c.platform.net import PageNet

    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    cancelled = threading.Event()

    def request(value):
        entered[value].set()
        assert release.wait(3)

    net = PageNet(SimpleNamespace(profile=SimpleNamespace(browser=SimpleNamespace())))
    monkeypatch.setattr(net, "_backend", lambda: SimpleNamespace(request_sync=request))
    monkeypatch.setattr(net, "build", lambda method, url, **kw: url)
    closer = threading.Thread(target=net.close)
    try:
        active = [net.request_async("GET", value) for value in (0, 1)]
        assert all(event.wait(3) for event in entered)
        queued = net.request_async("GET", 2)
        queued.add_done_callback(lambda _: cancelled.set())
        closer.start()
        assert cancelled.wait(3), "shutdown must complete the returned queued Future"
        assert queued.cancelled()
        release.set()
        closer.join(3)
        assert not closer.is_alive()
        assert all(future.done() for future in active)
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(3)
        net.close()
