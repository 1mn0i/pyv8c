"""XMLHttpRequest's deadline and its header block, offline: a page's own loader answers.

Both were read off Chrome 154 over a local server -- ``getAllResponseHeaders``
lower-cases the names, sorts them, ends every line with CRLF and leaves Set-Cookie out,
and a timeout reports ``loadend`` alone -- and a loader stands in for that server so the
rules stay covered on a machine with none.
"""
import threading

import pytest

pytest.importorskip("pyv8")

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
    page = browser.new_context().new_page()
    page.set_content(PAGE, url=ORIGIN + "/")
    return browser, page


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
        page.run_until_idle(timeout=10)
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
        page.run_until_idle(timeout=10)
        # abort() disarms the deadline and cancels the load, so the response that
        # arrives afterwards is dropped with the request rather than reported.
        assert page.evaluate("window.__log") == [["loadend"], 0]
    finally:
        released.set()
        browser.close()

