"""Regression tests for profile isolation and transport policy."""
import threading
from types import SimpleNamespace

import pytest

from pyv8c import Browser
from pyv8c.foundation.host_errors import HostStateError
from pyv8c.foundation.urls import origin_string, origin_tuple, serialize_origin
from pyv8c.network.assembly import assemble_backend
from pyv8c.network.cookies import CookieJar
from pyv8c.network.models import NetworkRequest, cookie_header


class HttpEngine:
    def __init__(self):
        self.closed = False
        self.cookies = None
    def request_sync(self, request): return None
    def close(self): self.closed = True


class WsEngine:
    def websocket(self, *args, **kwargs): return None
    def close(self): pass


def settings(**overrides):
    return SimpleNamespace(**({'real_network': True, 'http_engine': None, 'ws_engine': None,
                               'http_options': {}, 'ws_options': {}} | overrides))


def test_cookie_same_site_and_secure_origin():
    jar = CookieJar()
    url = 'https://bank.example/test'
    jar.add_from_response(url, 'strict=1; Secure; SameSite=Strict')
    jar.add_from_response(url, 'lax=1; Secure; SameSite=Lax')
    jar.add_from_response(url, 'none=1; Secure; SameSite=None')
    request = NetworkRequest(url=url, origin='https://attacker.test', credentials='include', method='POST')
    assert cookie_header(jar, request) == 'none=1'
    request.method = 'GET'
    request.mode = 'navigate'
    request.top_level_navigation = True
    assert cookie_header(jar, request) == 'lax=1; none=1'
    request.top_level_navigation = False
    assert cookie_header(jar, request) == 'none=1'
    request.origin = 'https://shop.bank.example'
    assert cookie_header(jar, request) == 'strict=1; lax=1; none=1'
    request.site_for_cookies = 'null'
    assert cookie_header(jar, request) == 'none=1'
    request.site_for_cookies = None
    jar.add_from_response('http://bank.example/', 'scheme=1; SameSite=Strict')
    cross_scheme = NetworkRequest(url='http://bank.example/', origin='https://bank.example', credentials='include')
    assert cookie_header(jar, cross_scheme) == ''
    jar.add_from_response('http://bank.example/', '__Host-session=bad; Secure; Path=/')
    assert '__Host-session' not in jar.header_for(url)
    jar.add_from_response(url, 'session=good; Secure; Path=/')
    jar.add_from_response('http://bank.example/', 'session=bad; Path=/')
    assert 'session=good' in jar.header_for(url)
    assert 'session=bad' not in jar.header_for(url)


def test_cookie_max_age_overflow_and_idna_origin():
    jar = CookieJar()
    jar.add_from_response('https://example.test/', 'x=1; Max-Age=' + '9' * 309)
    assert jar.header_for('https://example.test/') == 'x=1'
    url = 'https://例子.测试/'
    assert origin_string(url) == serialize_origin(origin_tuple(url)) == 'https://xn--fsqu00a.xn--0zwm56d'
    jar.add_from_response(url, 'idna=1; SameSite=Strict')
    request = NetworkRequest(url='https://xn--fsqu00a.xn--0zwm56d/', origin=url, credentials='include')
    assert cookie_header(jar, request) == 'idna=1'


def test_ready_engine_is_not_shared_between_profiles_or_browsers():
    engine = HttpEngine()
    first = Browser(real_network=True, http_engine=engine, ws_engine=None)
    second = Browser(real_network=True, http_engine=engine, ws_engine=None)
    try:
        profile = first.new_profile()
        with pytest.raises(HostStateError, match='one profile'):
            first.new_profile()
        with pytest.raises(HostStateError, match='another profile'):
            second.new_profile()
        assert engine.cookies is profile.cookies
        assert not engine.closed
    finally:
        first.close()
        second.close()


def test_factory_cannot_return_an_engine_already_owned_by_another_profile():
    engine = HttpEngine()
    browser = Browser(real_network=True, http_engine=lambda: engine, ws_engine=None)
    try:
        first = browser.new_profile()
        with pytest.raises(HostStateError, match='another profile'):
            browser.new_profile()
        assert first.network_backend.http is engine
        assert engine.cookies is first.cookies
        assert not engine.closed
    finally:
        browser.close()


def test_engine_role_validation_and_rollback():
    engine = HttpEngine()
    with pytest.raises(TypeError, match='websocket'):
        assemble_backend(settings(http_engine=engine, ws_engine=engine))
    assert not engine.closed
    with pytest.raises(TypeError, match='http_options'):
        assemble_backend(settings(ws_engine=WsEngine(), http_options={'timeout': 1}))
    instances = []
    def factory():
        item = HttpEngine(); instances.append(item); return item
    with pytest.raises(TypeError, match='websocket'):
        assemble_backend(settings(http_engine=factory, ws_engine=object()))
    assert len(instances) == 1 and instances[0].closed
    backend = assemble_backend(settings(http_engine=HttpEngine))
    try:
        assert isinstance(backend.http, HttpEngine)
    finally:
        backend.close()


def test_close_during_page_creation_releases_page(monkeypatch):
    from pyv8c.platform import browser as module
    entered, release = threading.Event(), threading.Event()
    made, results = [], []
    class SlowPage:
        def __init__(self, *args, **kwargs):
            self.closed = False; made.append(self); entered.set(); release.wait(5)
        def close(self): self.closed = True
    monkeypatch.setattr(module, 'Page', SlowPage)
    browser = Browser(real_network=False)
    try:
        profile = browser.new_profile()
        def create():
            try: profile.new_page()
            except BaseException as error: results.append(error)
        worker = threading.Thread(target=create)
        worker.start()
        assert entered.wait(3)
        profile.close()
        release.set(); worker.join(5)
        assert not worker.is_alive()
        assert len(results) == 1 and isinstance(results[0], HostStateError)
        assert made[0].closed and not profile.pages
    finally:
        release.set(); browser.close()


def test_accept_ch_is_keyed_by_response_origin():
    seen = []
    def loader(url, request):
        headers = {name.lower(): value for name, value in request['headers'].items()}
        seen.append((url, headers.get('sec-ch-ua-arch')))
        return {'status': 200, 'body': '<html></html>',
                'headers': {'accept-ch': 'Sec-CH-UA-Arch'} if 'site-a.test' in url else {}}
    browser = Browser(real_network=False, resource_loader=loader)
    try:
        page = browser.new_profile().new_page()
        page.goto('https://site-a.test/first')
        page.goto('https://site-b.test/')
        page.goto('https://site-a.test/second')
        assert seen[0][1] is None
        assert seen[1][1] is None
        assert seen[2][1] is not None
    finally:
        browser.close()


def test_real_cronet_does_not_send_client_jar_or_follow_manual_redirects():
    pytest.importorskip('cycronet')
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pyv8c.network.cronet import CronetTransport

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(302); self.send_header('Location', '/landing')
            else:
                self.send_response(200)
                self.send_header('Set-Cookie', 'strict=1; SameSite=Strict; Path=/')
                self.send_header('Set-Cookie', 'secret=2; HttpOnly; Path=/')
            self.end_headers()
            self.wfile.write(self.headers.get('Cookie', '').encode())
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = None
    try:
        transport = CronetTransport()
        base = f'http://127.0.0.1:{server.server_port}'
        response = transport.request_sync(NetworkRequest(base + '/set', credentials='include', redirect='manual'))
        assert len(response.headers['set-cookie']) == 2
        assert transport.cookies.document_for(base) == 'strict=1'
        response = transport.request_sync(NetworkRequest(base + '/cross', credentials='include',
                                                          origin='http://other.test', redirect='manual'))
        assert response.content == b''
        response = transport.request_sync(NetworkRequest(base + '/omit', credentials='omit', redirect='manual'))
        assert response.content == b''
        response = transport.request_sync(NetworkRequest(base + '/redirect', redirect='manual'))
        assert response.status == 302 and not response.redirected
    finally:
        if transport is not None: transport.close()
        server.shutdown(); server.server_close(); thread.join(5)


def test_page_fetch_enforces_same_site_cookies_on_real_http():
    pytest.importorskip('curl_cffi')
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get('Cookie', ''))
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', 'null'))
            self.send_header('Access-Control-Allow-Credentials', 'true')
            self.end_headers(); self.wfile.write(b'ok')
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    browser = Browser(real_network=True, ws_engine=None)
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        profile = browser.new_profile(); page = profile.new_page()
        profile.cookies.add_from_response(base, 'strict=1; SameSite=Strict; Path=/')
        page.set_content('<body></body>', url='http://other.test/')
        page.evaluate(f"window.__done=false; fetch('{base}/cross', {{credentials: 'include'}}).finally(() => window.__done=true)")
        page.wait_for('window.__done', timeout=5)
        same_page = profile.new_page()
        same_page.set_content('<body></body>', url=base + '/page')
        same_page.evaluate(f"window.__done=false; fetch('{base}/same', {{credentials: 'include'}}).finally(() => window.__done=true)")
        same_page.wait_for('window.__done', timeout=5)
        assert seen == ['', 'strict=1']
    finally:
        browser.close(); server.shutdown(); server.server_close(); thread.join(5)


def test_nontransitional_idna_keeps_origins_and_cookies_separate():
    from pyv8c.foundation.urls import same_origin
    source = 'https://faß.de/'
    assert origin_string(source) == 'https://xn--fa-hia.de'
    assert not same_origin(source, 'https://fass.de/')
    jar = CookieJar()
    jar.add_from_response(source, 'secret=1; Secure')
    assert jar.header_for('https://xn--fa-hia.de/') == 'secret=1'
    assert jar.header_for('https://fass.de/') == ''


def test_worker_plain_response_preserves_multivalue_headers():
    from pyv8c.network.models import NetworkResponse
    from pyv8c.platform.resources.session import plain_response
    original = ['a=1', 'b=2']
    response = plain_response(NetworkResponse(url='https://example.test/', headers={'set-cookie': original}),
                              'https://example.test/')
    assert response.headers['set-cookie'] == original
    assert response.headers['set-cookie'] is not original


def test_browser_close_waits_for_concurrent_profile_close():
    browser = Browser(real_network=False)
    profile = browser.new_profile()
    entered, release, browser_done = threading.Event(), threading.Event(), threading.Event()
    class Page:
        def close(self):
            entered.set()
            assert release.wait(5)
    profile.pages.append(Page())
    closer = threading.Thread(target=profile.close)
    browser_closer = threading.Thread(target=lambda: (browser.close(), browser_done.set()))
    try:
        closer.start()
        assert entered.wait(5)
        browser_closer.start()
        assert not browser_done.wait(0.1)
    finally:
        release.set()
        closer.join(5)
        if browser_closer.ident is not None:
            browser_closer.join(5)
    assert profile.closed and browser_done.is_set()


def test_cronet_redirect_recomputes_cookies(monkeypatch):
    import sys
    from pyv8c.network.cronet import CronetTransport
    seen = []
    class Client:
        def __init__(self, **kwargs): pass
        def get(self, url, **kwargs):
            seen.append((url, kwargs))
            if len(seen) == 1:
                return SimpleNamespace(url=url, status_code=302, reason='', content=b'', history=(),
                                       headers={'location': 'https://other.test/end'},
                                       _headers={'set-cookie': ['new=1; Secure']})
            return SimpleNamespace(url=url, status_code=200, reason='', content=b'ok', history=(), headers={})
        def close(self): pass
    monkeypatch.setitem(sys.modules, 'cycronet', SimpleNamespace(CronetClient=Client))
    transport = CronetTransport()
    try:
        transport.cookies.add_from_response('https://other.test/', 'target=1; Secure')
        response = transport.request_sync(NetworkRequest(url='https://source.test/', credentials='include',
                                                          headers={'Cookie': 'secret=1'}, redirect='follow'))
        assert response.redirected
        assert all(not kwargs['allow_redirects'] for _, kwargs in seen)
        assert seen[1][1]['headers']['Cookie'] == 'target=1'
        assert transport.cookies.header_for('https://source.test/') == 'new=1'
    finally:
        transport.close()
