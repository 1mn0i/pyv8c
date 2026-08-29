import pytest

from pyv8c import Browser, Response


def test_goto_downloads_html_and_waits_for_document_load():
    calls = []

    def loader(url, request):
        calls.append((url, request.get("mode")))
        if url.endswith("/app.js"):
            return Response("window.__downloaded = document.querySelector('#app').textContent", {"url": url})
        return Response(
            "<html><body><main id='app'>loaded</main><script src='/app.js'></script></body></html>",
            {"url": url, "headers": {"content-type": "text/html; charset=utf-8"}},
        )

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    response = page.goto("http://example.test/index.html")

    assert response.url == "http://example.test/index.html"
    assert page.evaluate("[document.URL, document.readyState, __downloaded]") == [
        "http://example.test/index.html", "complete", "loaded",
    ]
    assert calls == [
        ("http://example.test/index.html", "navigate"),
        ("http://example.test/app.js", "same-origin"),
    ]
    browser.close()


def test_new_page_url_and_location_assignment_navigate_automatically():
    pages = {
        "http://example.test/first": "<html><body><h1>first</h1></body></html>",
        "http://example.test/next": "<html><body><h1>next</h1></body></html>",
    }
    browser = Browser(resource_loader=lambda url, _request: Response(pages[url], {"url": url}))
    page = browser.new_context().new_page("http://example.test/first")

    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/first", "first",
    ]
    page.evaluate("location.href = '/next'")
    page.run_until_idle(timeout=1)
    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/next", "next",
    ]
    page.url = "/first"
    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/first", "first",
    ]
    browser.close()


def test_goto_decodes_http_charset_and_requires_a_loader_or_network_backend():
    html = "<html><body>中文页面</body></html>".encode("gb18030")
    browser = Browser(resource_loader=lambda url, _request: Response(
        html, {"url": url, "headers": {"content-type": "text/html; charset=gb18030"}},
    ))
    page = browser.new_context().new_page()
    page.goto("http://example.test/chinese")
    assert page.evaluate("document.body.textContent") == "中文页面"
    browser.close()

    browser = Browser()
    with pytest.raises(Exception, match="Network access requires"):
        browser.new_context().new_page("http://example.test/")
    browser.close()
