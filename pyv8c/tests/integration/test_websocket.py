"""``new WebSocket`` on the real ``websockets`` engine.

The unit tests drive the page's socket API with a fixture engine; these run a
real server, because what a socket engine is for is exactly what a fixture would
hide: the handshake a browser sends, the frames that cross the wire, and the
close handshake both sides have to agree on.
"""
import threading
import time
import base64
import hashlib
import socket
import ssl
import datetime
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("pyv8c.pyv8", reason="native pyv8 extension is not importable")
pytest.importorskip("websockets", reason="the default socket engine needs websockets")

from websockets.sync.server import serve

from pyv8c import Browser


def document(url, request):
    """Serve the page itself; the sockets are the point of these tests."""
    return {"body": "<body></body>", "headers": {"content-type": "text/html"}}


def wait_for(page, expression, timeout=5):
    """Turn the page's loop until *expression* holds, or give up.

    An open socket is not pending work, so a message arriving on one cannot be
    waited for by an idle wait alone.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.run_until_idle(timeout=0.1)
        if page.evaluate(expression):
            return True
    return False


class EchoServer:
    """A WebSocket server that echoes, and remembers what it was asked."""

    def __init__(self, subprotocols=None):
        self.handshakes = []
        self.handshake_ready = threading.Event()
        self.closed = []
        self._server = serve(self._handler, "127.0.0.1", 0, subprotocols=subprotocols)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/socket"

    def _serve(self):
        try:
            self._server.serve_forever()
        except Exception:  # shutdown closes the socket underneath it
            pass

    def _handler(self, connection):
        self.handshakes.append(dict(connection.request.headers))
        self.handshake_ready.set()
        try:
            for message in connection:
                connection.send(message)
        finally:
            self.closed.append(connection.close_code)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._thread.join(timeout=5)


@pytest.fixture
def echo():
    with EchoServer() as server:
        yield server


def test_a_page_socket_talks_to_a_real_server(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_profile().new_page("http://example.test/")
        page.evaluate(f"""
            window.__events = [];
            const ws = new WebSocket('{echo.url}');
            ws.onopen = () => {{ window.__events.push(['open', ws.protocol]); ws.send('hello'); }};
            ws.onmessage = e => {{ window.__events.push(['message', e.data, typeof e.data]); ws.close(1000, 'done'); }};
            ws.onclose = e => window.__events.push(['close', e.code, e.reason, e.wasClean]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 3"), page.evaluate("__events")
        assert page.evaluate("__events") == [
            ["open", ""], ["message", "hello", "string"], ["close", 1000, "done", True],
        ]
        assert page.evaluate("__ws.readyState") == 3
        assert not page.services._ownership.websockets
    finally:
        browser.close()


def test_binary_frames_survive_the_round_trip(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_profile().new_page("http://example.test/")
        # A Blob built from a string carries its bytes; a Blob or a send built
        # from a bare typed array does not -- ``bytes_from_value`` falls through
        # to ``bytes(value)``, which iterates a JavaScript object: its indices,
        # not its elements.  That defect is the Blob constructor's, not this
        # engine's, and it is not what these tests are about.
        page.evaluate(f"""
            window.__events = [];
            const ws = new WebSocket('{echo.url}');
            ws.binaryType = 'arraybuffer';
            ws.onopen = () => ws.send(new Blob(['abc']));
            ws.onmessage = e => window.__events.push([e.data instanceof ArrayBuffer, Array.from(new Uint8Array(e.data))]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 1"), page.evaluate("__events")
        assert page.evaluate("__events") == [[True, [97, 98, 99]]]
    finally:
        browser.close()


def test_a_handshake_carries_the_browser_headers_and_the_jar(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        context = browser.new_profile()
        origin = 'http://' + urlsplit(echo.url).netloc
        page = context.new_page(origin + '/')
        # A page sets cookies for its own host only, so the jar is filled the way
        # a response from the socket's host would have filled it.
        context.cookies.set_from_document(echo.url.replace("ws://", "http://"), "sid=1; path=/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        assert echo.handshake_ready.wait(5)
        headers = echo.handshakes[-1]
        # A browser's handshake, not Python's: the document's origin, the
        # environment's headers and the context's cookies for the target.
        assert headers["origin"] == origin
        assert headers["user-agent"].startswith("Mozilla/5.0")
        assert "zh-CN" in headers["accept-language"]
        assert headers["cookie"] == "sid=1"
    finally:
        browser.close()


def test_cross_site_websocket_omits_lax_cookie(echo):
    browser = Browser(ws_engine='websockets', resource_loader=document)
    try:
        context = browser.new_profile()
        page = context.new_page('http://example.test/')
        context.cookies.set_from_document(echo.url.replace('ws://', 'http://'), 'sid=1; path=/; SameSite=Lax')
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, '__ws.readyState === 1')
        assert echo.handshake_ready.wait(5)
        assert 'cookie' not in echo.handshakes[-1]
    finally:
        browser.close()


def test_the_handshake_can_leave_the_cookies_behind(echo):
    browser = Browser(
        ws_engine="websockets", ws_options={"cookies": False}, resource_loader=document,
    )
    try:
        context = browser.new_profile()
        page = context.new_page("http://example.test/")
        context.cookies.set_from_document(echo.url.replace("ws://", "http://"), "sid=1; path=/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        assert echo.handshake_ready.wait(5)
        assert "cookie" not in echo.handshakes[-1]
    finally:
        browser.close()


def test_extra_handshake_headers_come_from_the_options(echo):
    browser = Browser(
        ws_engine="websockets", ws_options={"headers": {"X-Page": "socket"}}, resource_loader=document,
    )
    try:
        page = browser.new_profile().new_page("http://example.test/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        assert echo.handshake_ready.wait(5)
        assert echo.handshakes[-1]["x-page"] == "socket"
    finally:
        browser.close()


def test_a_subprotocol_is_negotiated():
    with EchoServer(subprotocols=["chat"]) as server:
        browser = Browser(ws_engine="websockets", resource_loader=document)
        try:
            page = browser.new_profile().new_page("http://example.test/")
            page.evaluate(f"window.__ws = new WebSocket('{server.url}', ['chat'])")
            assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
            assert page.evaluate("__ws.protocol") == "chat"
        finally:
            browser.close()


def test_a_socket_that_cannot_connect_reports_error_then_close():
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_profile().new_page("http://example.test/")
        page.evaluate("""
            window.__events = [];
            const ws = new WebSocket('ws://127.0.0.1:1/');
            ws.onerror = () => window.__events.push('error');
            ws.onclose = e => window.__events.push(['close', e.code, e.wasClean]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 2"), page.evaluate("__events")
        assert page.evaluate("__events") == ["error", ["close", 1006, False]]
    finally:
        browser.close()


def test_closing_the_context_closes_the_socket(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_profile().new_page("http://example.test/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
    finally:
        browser.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not echo.closed:
        time.sleep(0.05)
    # The engine's own closing handshake, not a dropped socket.
    assert echo.closed == [1000]


def test_close_while_handshake_is_pending_never_reopens_socket(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    closed = []

    class LateConnection:
        def close(self, code, reason):
            closed.append((code, reason))

    def connect(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return LateConnection()

    monkeypatch.setattr('pyv8c.network.websockets._connect_client', lambda: connect)
    browser = Browser(ws_engine='websockets', resource_loader=document)
    try:
        page = browser.new_profile().new_page('http://example.test/')
        page.evaluate("""
            globalThis.events = [];
            globalThis.ws = new WebSocket('ws://example.test/');
            ws.onopen = () => events.push('open');
            ws.onerror = () => events.push('error');
            ws.onclose = e => events.push(['close', e.code, e.wasClean]);
        """)
        assert entered.wait(3)
        page.evaluate("ws.close(1000, 'cancelled')")
        assert page.evaluate('ws.readyState') == 2
        release.set()
        page.wait_for('events.length === 2', timeout=5)
        assert page.evaluate('events') == ['error', ['close', 1006, False]]
        assert page.evaluate('ws.readyState') == 3
        assert closed == [(1000, 'cancelled')]
    finally:
        release.set()
        browser.close()


def test_close_after_real_handshake_before_open_task(echo, monkeypatch):
    from pyv8c.network.websockets import _Socket

    posted = threading.Event()
    original = _Socket._deliver

    def observe(self, name, *args):
        original(self, name, *args)
        if name == 'open':
            posted.set()

    monkeypatch.setattr(_Socket, '_deliver', observe)
    browser = Browser(ws_engine='websockets', resource_loader=document)
    try:
        page = browser.new_profile().new_page('http://example.test/')
        page.executor.call(lambda: page.v8_context.expose(
            wait_for_transport=lambda: posted.wait(3)))
        assert page.evaluate(f"""
            globalThis.events=[];
            globalThis.ws=new WebSocket('{echo.url}');
            ws.onopen=()=>events.push('open');
            ws.onmessage=()=>events.push('message');
            ws.onerror=()=>{{events.push(['error',ws.readyState]);
                queueMicrotask(()=>events.push('microtask'));}};
            ws.onclose=e=>events.push(['close',e.code,e.reason,e.wasClean,ws.readyState]);
            const connected=wait_for_transport();
            events.push(['before',ws.readyState]);
            ws.close(1000,'cancel');
            events.push(['after',ws.readyState]);
            connected;
        """)
        page.wait_for('events.length===5', timeout=5)
        assert page.evaluate('events') == [
            ['before', 0], ['after', 2], ['error', 3], 'microtask',
            ['close', 1006, '', False, 3],
        ]
        assert not page.services._ownership.websockets
    finally:
        browser.close()


def test_slow_socket_writer_does_not_block_page_tasks(echo, monkeypatch):
    from websockets.sync.client import ClientConnection

    entered, release = threading.Event(), threading.Event()
    original = ClientConnection.send

    def slow_send(self, data, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(ClientConnection, 'send', slow_send)
    browser = Browser(ws_engine='websockets', resource_loader=document)
    try:
        page = browser.new_profile().new_page('http://example.test/')
        page.evaluate(f"globalThis.ws=new WebSocket('{echo.url}'); 'created'")
        page.wait_for('ws.readyState===1', timeout=5)
        assert page.evaluate("ws.send('中');ws.bufferedAmount") == 3
        assert entered.wait(3)
        page.evaluate('globalThis.timerRan=false;setTimeout(()=>timerRan=true,0)')
        page.wait_for('timerRan', timeout=2)
        assert page.evaluate('ws.bufferedAmount') == 3
        release.set()
        page.wait_for('ws.bufferedAmount===0', timeout=5)
    finally:
        release.set()
        browser.close()


@pytest.mark.parametrize('close_first', [False, True])
@pytest.mark.parametrize('secure', [False, True])
def test_page_close_interrupts_real_unread_websocket(monkeypatch, close_first, secure, tmp_path):
    from websockets.sync.client import ClientConnection

    if secure:
        pytest.importorskip('cryptography')

    release, entered, finished = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            accept = base64.b64encode(hashlib.sha1(key.encode('ascii')).digest()).decode('ascii')
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            self.wfile.flush()
            release.wait(30)  # Complete the handshake, then deliberately stop reading.
            self.close_connection = True

        def log_message(self, *args):
            pass

    original = ClientConnection.send

    def observed_send(self, data, *args, **kwargs):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 0)
        entered.set()
        try:
            return original(self, data, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(ClientConnection, 'send', observed_send)
    options = {}
    server_context = None
    if secure:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
        cert_path, key_path = tmp_path / 'cert.pem', tmp_path / 'key.pem'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                             serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE  # Only this local self-signed fixture.
        options['ssl'] = client_context
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if server_context is not None:
        try:
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
        except BaseException:
            server.server_close()
            raise
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = Browser(ws_engine='websockets', ws_options=options, resource_loader=document)
    try:
        page = browser.new_profile().new_page('http://example.test/')
        scheme = 'wss' if secure else 'ws'
        page.evaluate(f"globalThis.ws=new WebSocket('{scheme}://127.0.0.1:{server.server_port}/');'created'")
        page.wait_for('ws.readyState===1', timeout=5)
        engine = page.network_backend.ws
        with engine._lock:
            transport, = engine._sockets
        connection = transport._connection
        page.evaluate("ws.send(new Blob(['x'.repeat(4*1024*1024)]));'sent'")
        assert entered.wait(3)
        assert not finished.wait(0.1), 'fixture did not block the actual WebSocket write'
        if close_first:
            page.evaluate('ws.close()')
        page.close()
        transport._thread.join(3)
        transport._io_thread.join(3)
        assert not transport._thread.is_alive()
        assert not transport._io_thread.is_alive()
        connection.recv_events_thread.join(3)
        assert not connection.recv_events_thread.is_alive()
        if connection.keepalive_thread is not None:
            connection.keepalive_thread.join(3)
            assert not connection.keepalive_thread.is_alive()
        assert not transport._callbacks
        assert not engine._sockets
        assert not page.services._ownership.websockets
    finally:
        release.set()
        browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join(5)
        assert not server_thread.is_alive()
