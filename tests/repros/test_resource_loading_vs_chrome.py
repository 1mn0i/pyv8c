"""Request mode and source policy, with loader and HTTP against local Chrome."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlsplit

import pytest

pytest.importorskip("pyv8")
from harness import chrome_reference, ReferenceUnavailable


@pytest.fixture(scope="module")
def reference():
    try:
        with chrome_reference() as session:
            yield session
    except ReferenceUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def origins():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if self.path != "/blocked":
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/html" if self.path == "/page" else "text/plain")
            self.end_headers()
            body = "<body>local</body>" if self.path == "/page" else self.headers.get("Sec-Fetch-Mode", "missing")
            self.wfile.write(body.encode())
        def log_message(self, *args):
            pass
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    try:
        yield [f"http://127.0.0.1:{s.server_port}" for s in servers]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()


@pytest.mark.parametrize("transport", ["loader", "http"])
@pytest.mark.parametrize("source,mode", [("same", "same-origin"), ("same", "no-cors"),
                                         ("cross", "same-origin"), ("cross", "cors"),
                                         ("blocked", "cors"), ("cross", "no-cors"), ("data", "cors")])
def test_source_and_mode_match_chrome(reference, origins, transport, source, mode):
    from pyv8c import Browser
    same, other = origins
    target = {"same": same + "/probe", "cross": other + "/probe",
              "blocked": other + "/blocked", "data": "data:text/plain,local"}[source]
    script = """window.__answer=null; fetch(%s, {mode:%s}).then(r => r.text().then(text => {
        window.__answer={status:r.status,text:text};
    })).catch(e => {window.__answer={error:e.name};}); 'started'""" % (json.dumps(target), json.dumps(mode))
    chrome = reference.browser.new_page()
    try:
        chrome.goto(same + "/page")
        chrome.evaluate(script)
        chrome.wait_for_function("window.__answer !== null")
        expected = chrome.evaluate("window.__answer")
    finally:
        chrome.close()
    def loader(url, request):
        path = urlsplit(url).path
        return {"body": "<body>local</body>" if path == "/page" else request["headers"].get("Sec-Fetch-Mode", "missing"),
                "headers": {} if path == "/blocked" else {"access-control-allow-origin": "*"}}
    browser = Browser(real_network=transport == "http", resource_loader=loader if transport == "loader" else None,
                      stub_logging="off")
    try:
        page = browser.new_profile().new_page(same + "/page")
        page.evaluate(script)
        for _ in range(10):
            page.run_until_idle(1)
            result = page.evaluate("JSON.stringify(window.__answer)")
            if result != "null":
                break
        assert json.loads(result) == expected
    finally:
        browser.close()


@pytest.mark.parametrize("mode", ["cors", "no-cors", "same-origin"])
@pytest.mark.parametrize("cross_origin", [False, True])
def test_blob_fetch_checks_creator_origin(reference, origins, mode, cross_origin):
    from pyv8c import Browser
    creator_origin, other = origins
    reader_origin = other if cross_origin else creator_origin
    script = """window.__answer=null; fetch(%s, {mode:%s}).then(r => r.text().then(text => {
        window.__answer={text:text};
    })).catch(e => {window.__answer={error:e.name};}); 'started'"""
    chrome_context = reference.browser.new_context()
    creator = chrome_context.new_page()
    reader = chrome_context.new_page()
    try:
        creator.goto(creator_origin + "/page")
        reader.goto(reader_origin + "/page")
        url = creator.evaluate("URL.createObjectURL(new Blob(['secret']))")
        reader.evaluate(script % (json.dumps(url), json.dumps(mode)))
        reader.wait_for_function("window.__answer !== null")
        expected = reader.evaluate("window.__answer")
    finally:
        chrome_context.close()
    browser = Browser(real_network=False, stub_logging="off")
    try:
        context = browser.new_profile()
        creator, reader = context.new_page(), context.new_page()
        creator.set_content("<body></body>", url=creator_origin + "/page")
        reader.set_content("<body></body>", url=reader_origin + "/page")
        url = creator.evaluate("URL.createObjectURL(new Blob(['secret']))")
        reader.evaluate(script % (json.dumps(url), json.dumps(mode)))
        reader.run_until_idle(2)
        assert json.loads(reader.evaluate("JSON.stringify(window.__answer)")) == expected
    finally:
        browser.close()
