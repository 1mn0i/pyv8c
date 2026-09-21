"""Headers, Request and Response: the parts of fetch a script can use at once."""
import pytest

from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(stub_logging="off")
    instance = browser.new_context().new_page()
    instance.set_content("<body></body>", url="http://fetch.test/")
    try:
        yield instance
    finally:
        browser.close()


def test_headers_lookup_combines_and_iterates_sorted(page):
    assert page.evaluate("""
        (() => {
            const headers = new Headers([['b', '2'], ['a', '1']]);
            headers.append('a', '3');
            return [headers.get('A'), headers.has('B'), Array.from(headers.keys()),
                    Array.from(headers.values()), Array.from(headers.entries())];
        })()
    """) == ["1, 3", True, ["a", "b"], ["1, 3", "2"], [["a", "1, 3"], ["b", "2"]]]


def test_response_reports_its_default_type_and_clones_independently(page):
    assert page.evaluate("""
        (() => {
            const response = new Response('hello', {status: 201, headers: {'x-a': '1'}});
            const copy = response.clone();
            copy.headers.set('x-a', '2');
            return [response.type, response.status, response.ok, response.bodyUsed,
                    copy.bodyUsed, response.headers.get('x-a'), copy.headers.get('x-a')];
        })()
    """) == ["default", 201, True, False, False, "1", "2"]


def test_request_reports_its_body_and_destination_state(page):
    assert page.evaluate("""
        (() => {
            const request = new Request('/x', {method: 'post'});
            return [request.url, request.method, request.bodyUsed, request.destination,
                    request.mode];
        })()
    """) == ["http://fetch.test/x", "POST", False, "", "cors"]
