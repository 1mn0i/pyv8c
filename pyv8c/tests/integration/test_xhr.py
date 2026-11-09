"""XMLHttpRequest's deadline and its header block, offline: a page's own loader answers.

Both were read off Chrome 154 over a local server -- ``getAllResponseHeaders``
lower-cases the names, sorts them, ends every line with CRLF and leaves Set-Cookie out,
and a timeout reports ``loadend`` alone -- and a loader stands in for that server so the
rules stay covered on a machine with none.
"""
import threading

import pytest

pytest.importorskip("pyv8c.pyv8")

from pyv8c import Browser

ORIGIN = "http://xhr.test"
PAGE = "<!doctype html><title>xhr</title><body></body>"
HEADERS = {"Content-Type": "text/plain", "X-Mixed-Case": "One", "Set-Cookie": "a=1",
           "Cache-Control": "no-store", "Content-Length": "2"}
BLOCK = ("cache-control: no-store\r\ncontent-length: 2\r\ncontent-type: text/plain\r\n"
         "x-mixed-case: One\r\n")


def _loader(url, request, released=None):
    if released is not None and "slow" in str(url):
        released.wait(5)
    return {"status": 200, "url": str(url), "body": "ok", "headers": HEADERS}


def _page(loader):
    browser = Browser(stub_logging="off", threaded_loaders=True, resource_loader=loader)
    page = browser.new_profile().new_page()
    page.set_content(PAGE, url=ORIGIN + "/")
    return browser, page


def test_arraybuffer_response_keeps_raw_bytes_and_is_available_only_when_done():
    payload = b'!\x00\x80\xff\xc3\xa9'
    def loader(url, request):
        return {'status': 200, 'body': payload, 'headers': {'content-type': 'application/octet-stream'}}
    browser, page = _page(loader)
    try:
        page.evaluate('''
          globalThis.result=null;globalThis.states=[];
          const xhr=new XMLHttpRequest();xhr.open('GET','/binary');xhr.responseType='arraybuffer';
          globalThis.before=xhr.response===null;
          xhr.onreadystatechange=()=>states.push([xhr.readyState,xhr.response===null]);
          xhr.onload=()=>{
            const body=xhr.response;let textError='';
            try{void xhr.responseText}catch(e){textError=e.name}
            result=[body instanceof ArrayBuffer,body===xhr.response,Array.from(new Uint8Array(body)),textError];
          };xhr.send();
        ''')
        page.wait_for('result!==null', timeout=3)
        assert page.evaluate('before') is True
        assert page.evaluate('states') == [[2, True], [3, True], [4, False]]
        assert page.evaluate('result') == [True, True, list(payload), 'InvalidStateError']
    finally:
        browser.close()


def test_reopening_xhr_drops_cached_arraybuffer_without_changing_old_buffer():
    def loader(url, request):
        return {'status': 200, 'body': b'AB' if url.endswith('/first') else b'\x00\xff'}
    browser, page = _page(loader)
    try:
        page.evaluate('''
          globalThis.result=null;const xhr=new XMLHttpRequest();let first;
          xhr.open('GET','/first');xhr.responseType='arraybuffer';
          xhr.onload=()=>{
            if(!first){
              first=xhr.response;new Uint8Array(first)[0]=90;
              globalThis.same=xhr.response===first&&new Uint8Array(xhr.response)[0]===90;
              xhr.open('GET','/second');globalThis.reset=xhr.response===null;xhr.send();
            }else result=[same,reset,xhr.response!==first,
                          Array.from(new Uint8Array(first)),Array.from(new Uint8Array(xhr.response))];
          };xhr.send();
        ''')
        page.wait_for('result!==null', timeout=3)
        assert page.evaluate('result') == [True, True, True, [90, 66], [0, 255]]
    finally:
        browser.close()


def test_request_guard_filters_forbidden_headers_and_xhr_combines_values():
    requests = []
    def loader(url, request):
        requests.append((url, request))
        return _loader(url, request)

    browser, page = _page(loader)
    try:
        assert page.evaluate("""(() => {
            const standalone = new Headers({Cookie: 'standalone'});
            const request = new Request('/data', {headers: {Cookie: 'bad', 'X-Test': 'one'}});
            request.headers.set('Sec-Fetch-Site', 'bad');
            request.headers.set('Permissions-Policy', 'allowed');
            request.headers.append('X-Test', 'two');
            return [standalone.get('cookie'), request.headers.get('cookie') === null,
                    request.headers.get('sec-fetch-site') === null, request.headers.get('x-test'),
                    request.headers.get('permissions-policy')];
        })()""") == ['standalone', True, True, 'one, two', 'allowed']
        page.evaluate("""(() => {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/data');
            xhr.setRequestHeader('Cookie', 'bad');
            xhr.setRequestHeader('X-Test', 'one');
            xhr.setRequestHeader('X-Test', 'two');
            xhr.send();
        })()""")
        page.run_until_idle(timeout=5)
        data = next(request for url, request in requests if url.endswith('/data'))
        headers = {name.lower(): value for name, value in data['headers'].items()}
        assert headers.get('cookie') != 'bad'
        assert headers['x-test'] == 'one, two'
    finally:
        browser.close()


def test_get_all_response_headers_writes_the_block_chrome_writes():
    browser, page = _page(_loader)
    try:
        page.evaluate("""(() => {
            window.__block = null;
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/data');
            xhr.onload = () => {
                window.__block = [xhr.getAllResponseHeaders(),
                                  xhr.getResponseHeader('content-TYPE'),
                                  xhr.getAllResponseHeaders() === xhr.getAllResponseHeaders()];
            };
            xhr.send();
            return 'asked';
        })()""")
        page.run_until_idle(timeout=5)
        assert page.evaluate("window.__block") == [BLOCK, "text/plain", True]
    finally:
        browser.close()


def test_get_all_response_headers_is_empty_until_a_response_arrives():
    browser, page = _page(_loader)
    try:
        assert page.evaluate("""(() => {
            const fresh = new XMLHttpRequest();
            const opened = new XMLHttpRequest();
            opened.open('GET', '/data');
            return [fresh.getAllResponseHeaders(), fresh.readyState, fresh.timeout,
                    opened.getAllResponseHeaders()];
        })()""") == ["", 0, 0, ""]
    finally:
        browser.close()


def test_a_timeout_drops_the_request_and_reports_loadend_alone():
    released = threading.Event()
    browser, page = _page(lambda url, request: _loader(url, request, released))
    try:
        page.evaluate("""(() => {
            window.__log = null;
            const xhr = new XMLHttpRequest();
            const log = [];
            xhr.onload = () => log.push('load');
            xhr.onerror = () => log.push('error');
            xhr.onabort = () => log.push('abort');
            xhr.onloadend = () => log.push('loadend');
            xhr.timeout = 50;
            xhr.open('GET', '/slow');
            xhr.send();
            setTimeout(() => {
                window.__log = [log, xhr.readyState, xhr.status, xhr.timeout,
                                xhr.getAllResponseHeaders()];
            }, 400);
            return 'asked';
        })()""")
        page.wait_for("window.__log !== null", timeout=10)
        assert page.evaluate("window.__log") == [["loadend"], 4, 0, 50, ""]
    finally:
        released.set()
        browser.close()


def test_abort_drops_a_request_that_is_still_running():
    released = threading.Event()
    browser, page = _page(lambda url, request: _loader(url, request, released))
    try:
        page.evaluate("""(() => {
            window.__log = null;
            const xhr = new XMLHttpRequest();
            const log = [];
            xhr.onload = () => log.push('load');
            xhr.onloadend = () => log.push('loadend');
            xhr.open('GET', '/slow');
            xhr.timeout = 40;
            xhr.send();
            xhr.abort();
            setTimeout(() => { window.__log = [log, xhr.readyState]; }, 300);
            return 'asked';
        })()""")
        page.wait_for("window.__log !== null", timeout=10)
        # abort() disarms the deadline and cancels the load, so the response that
        # arrives afterwards is dropped with the request rather than reported.
        assert page.evaluate("window.__log") == [["loadend"], 0]
    finally:
        released.set()
        browser.close()


def test_send_gives_a_string_body_the_content_type_chrome_gives_it():
    """A string body is ``text/plain;charset=UTF-8`` unless the author said otherwise.

    Chrome 154 sends exactly that on the Cloudflare challenge's own submit POST, and the answer
    that lets its widget take the last step -- ``cf-chl-out`` -- comes back only when it does;
    the engine sent no ``Content-Type`` at all, so the widget stopped one step short of
    ``cf_clearance`` (measured 2026-10-07).  A Blob brings its own type, and no body carries
    none -- the same rule ``sendBeacon`` already followed.
    """
    seen = []

    def loader(url, request):
        seen.append((str(url), dict(request or {})))
        return {"status": 200, "url": str(url), "body": "ok", "headers": {}}

    browser, page = _page(loader)
    try:
        page.evaluate("""(() => {
            const plain = new XMLHttpRequest();
            plain.open('POST', '/plain');
            plain.send('a=1');
            const typed = new XMLHttpRequest();
            typed.open('POST', '/typed');
            typed.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
            typed.send('a=1');
            const blob = new XMLHttpRequest();
            blob.open('POST', '/blob');
            blob.send(new Blob(['x'], {type: 'text/csv'}));
            const empty = new XMLHttpRequest();
            empty.open('GET', '/empty');
            empty.send();
            return 'asked';
        })()""")
        page.run_until_idle(timeout=5)
    finally:
        browser.close()

    def sent_type(name):
        for url, request in seen:
            if url.endswith(name):
                return request.get("content_type")
        raise AssertionError(f"no request to {name}; saw {[url for url, _ in seen]}")

    assert sent_type("/plain") == "text/plain;charset=UTF-8"
    assert sent_type("/typed") is None, "the author's own Content-Type must stand"
    assert sent_type("/blob") == "text/csv"
    assert sent_type("/empty") is None, "no body, no type"

