import sys
import types

import pytest

from pyv8c import Browser, Response


class FakeResponse:
    status_code = 200
    reason = "OK"
    headers = {"content-type": "text/plain"}
    content = b"cronet-ok"
    url = "http://example.test/data"
    history = ()


class FakeClient:
    def __init__(self, **options):
        self.options = options
        self.closed = False

    def get(self, url, **kwargs):
        return FakeResponse()

    def close(self):
        self.closed = True

    def websocket(self, url, protocols=None, on_open=None, on_message=None, on_close=None, on_error=None):
        return FakeWebSocket(on_open, on_message, on_close, on_error)


class FakeWebSocket:
    protocol = "fixture"
    def __init__(self, on_open, on_message, on_close, on_error):
        self._on_open, self._on_message, self._on_close, self._on_error = on_open, on_message, on_close, on_error
        self.closed = False
    def run_in_background(self): self._on_open(self)
    def send(self, value): self._on_message(self, value, True)
    def send_bytes(self, value): self._on_message(self, value, False)
    def close(self, code=1000, reason=""):
        if not self.closed:
            self.closed = True; self._on_close(self, code, reason, True)


def install_fake_cycronet(monkeypatch):
    monkeypatch.setitem(sys.modules, "cycronet", types.SimpleNamespace(CronetClient=FakeClient))


def test_real_network_requires_optional_dependency(monkeypatch):
    monkeypatch.setattr("pyv8c.web.network.backend.importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError("missing")))
    with pytest.raises(RuntimeError, match="cycronet"):
        Browser(real_network=True).new_context()


def test_real_network_fetch_is_delivered_on_page_queue(monkeypatch):
    install_fake_cycronet(monkeypatch)
    browser = Browser(real_network=True, cycronet_options={"verify": True})
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    # The response is delivered on the page task queue, never inline: even a
    # fixture-short round trip resolves after the current script has ended.
    assert page.evaluate(
        "fetch('/data').then(r => r.text()).then(v => window.__result = v), typeof window.__result"
    ) == "undefined"
    page.run_until_idle(timeout=2)
    assert page.evaluate("window.__result") == "cronet-ok"
    browser.close()


def test_resource_loader_takes_precedence_over_real_network(monkeypatch):
    install_fake_cycronet(monkeypatch)
    browser = Browser(real_network=True, resource_loader=lambda url, request: Response("fixture", {"url": url}))
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    assert page.evaluate("fetch('/data').then(r => r.text()).then(v => window.__result = v)") is not None
    page.run_until_idle()
    assert page.evaluate("window.__result") == "fixture"
    browser.close()


def test_websocket_events_are_delivered_on_page_queue(monkeypatch):
    install_fake_cycronet(monkeypatch)
    browser = Browser(real_network=True)
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    page.evaluate("window.__events=[]; const ws=new WebSocket('ws://example.test/'); ws.onopen=()=>__events.push('open'); ws.onmessage=e=>__events.push(e.data); ws.onclose=()=>__events.push('close'); window.__ws=ws")
    page.run_until_idle()
    assert page.evaluate("__events") == ["open"]
    page.evaluate("__ws.send('hello'); __ws.close(1000, 'done')")
    page.run_until_idle()
    assert page.evaluate("__events") == ["open", "hello", "close"]
    assert page.evaluate("[WebSocket.prototype.__proto__ === EventTarget.prototype, WebSocket.OPEN, WebSocket.CLOSED]") == [True, 1, 3]
    browser.close()


def test_websocket_binary_types_and_blob_api(monkeypatch):
    install_fake_cycronet(monkeypatch)
    browser = Browser(real_network=True)
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    page.evaluate("window.__binary=[]; const ws=new WebSocket('ws://example.test/'); ws.binaryType='blob'; ws.onopen=()=>ws.send(new Blob(['abc'], {type:'application/test'})); ws.onmessage=e=>__binary.push([e.data instanceof Blob,e.data instanceof ArrayBuffer,e.data instanceof Blob ? e.data.size : e.data.byteLength]); window.__ws=ws")
    page.run_until_idle()
    assert page.evaluate("__binary") == [[True, False, 3]]
    assert page.evaluate("new Blob(['hello'], {type:'text/plain'}).slice(1,4).text().then(v=>window.__blob=v)") is not None
    page.run_until_idle()
    assert page.evaluate("__blob") == "ell"
    page.evaluate("new Blob(['abc']).arrayBuffer().then(v=>window.__buffer=Array.from(new Uint8Array(v)).join(','))")
    page.run_until_idle()
    assert page.evaluate("__buffer") == "97,98,99"
    page.evaluate("__ws.binaryType='arraybuffer'; __ws.send(new Uint8Array([1,2,255]))")
    page.run_until_idle()
    assert page.evaluate("__binary[1]") == [False, True, 3]
    browser.close()


def test_cross_origin_cors_and_no_cors(monkeypatch):
    install_fake_cycronet(monkeypatch)
    class CORSClient(FakeClient):
        def get(self, url, **kwargs):
            response = FakeResponse(); response.url = url
            response.headers = {"access-control-allow-origin": "http://example.test"}
            return response
    monkeypatch.setitem(sys.modules, "cycronet", types.SimpleNamespace(CronetClient=CORSClient))
    browser = Browser(real_network=True); page = browser.new_context().new_page(); page.set_content("<body></body>", url="http://example.test/")
    page.evaluate("fetch('http://other.test/data').then(r => r.text()).then(v => window.__cors = v)")
    page.run_until_idle(timeout=2)
    assert page.evaluate("__cors") == "cronet-ok"
    page.evaluate("fetch('http://other.test/data', {mode:'no-cors'}).then(r => window.__opaque = [r.type, r.status])")
    page.run_until_idle(timeout=2)
    assert page.evaluate("__opaque") == ["opaque", 0]
    browser.close()


def test_worker_executes_blob_script_and_uses_independent_queue():
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    result = page.evaluate("(() => { const src = new Blob([\"self.onmessage = e => self.postMessage({value:e.data.value + 1, bytes:e.data.bytes, helper:typeof __pyv8c_worker_value, windowProps:typeof WindowProperties});\"], {type:'text/javascript'}); const url=URL.createObjectURL(src); const w=new Worker(url); window.__workerInstance=w; w.onmessage=e=>window.__worker=e.data; w.onerror=e=>window.__workerError=e.message; w.postMessage({value:4, bytes:new Uint8Array([1,2,3])}); return [url.startsWith('blob:'), w instanceof Worker]; })()")
    assert result == [True, True]
    page.run_until_idle(timeout=2)
    assert page.evaluate("[typeof __worker, typeof __workerError]") == ["object", "undefined"]
    assert page.evaluate("__worker.value") == 5
    assert page.evaluate("[__worker.helper, __worker.windowProps]") == ["undefined", "undefined"]
    assert page.evaluate("Array.from(new Uint8Array(__worker.bytes))") == [1, 2, 3]
    page.evaluate("URL.revokeObjectURL(URL.createObjectURL(new Blob(['x'])))")
    page.evaluate("window.__workerBeforeTerminate = __worker; window.__workerInstance.terminate()")
    page.run_until_idle()
    assert page.evaluate("__worker === __workerBeforeTerminate") is True
    browser.close()


def test_worker_self_is_the_context_global_without_explicit_expose():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    page.evaluate("""(() => {
      const source = new Blob([
        "postMessage([self === globalThis, typeof self, typeof globalThis, typeof window, Object.prototype.hasOwnProperty.call(globalThis, 'self')]);"
      ], {type: 'text/javascript'});
      const worker = new Worker(URL.createObjectURL(source));
      worker.onmessage = event => window.__workerGlobals = event.data;
    })()""")
    page.run_until_idle(timeout=2)
    assert page.evaluate("__workerGlobals") == [True, "object", "object", "undefined", False]
    browser.close()
