"""URL parsing and its search params, on a page that has a real origin."""
import pytest

from pyv8c import Browser

PAGE = "<body></body>"


@pytest.fixture
def page():
    browser = Browser(stub_logging="off")
    instance = browser.new_context().new_page()
    instance.set_content(PAGE, url="https://example.test/dir/page")
    try:
        yield instance
    finally:
        browser.close()


def test_a_space_is_percent_encoded_where_a_browser_encodes_it(page):
    assert page.evaluate("""
        (() => {
            const made = ['https://a.te st/', 'https://u s@a.test/', 'https://a.test:80 80/'];
            return [new URL('../x?q=1 2', 'https://example.test/dir/page').href,
                    new URL('https://a.test/p q#f 3').href,
                    new URL('  https://a.test/ok\\t').href,
                    made.map(text => {
                        try { return new URL(text).href; } catch (error) { return 'REJECTED'; }
                    })];
        })()
    """) == ["https://example.test/x?q=1%202", "https://a.test/p%20q#f%203",
             "https://a.test/ok",
             ["https://a.te%20st/", "https://u%20s@a.test/", "REJECTED"]]


def test_search_params_views_keep_the_order_and_the_duplicates(page):
    assert page.evaluate("""
        (() => {
            const params = new URLSearchParams('a=1&b=2&a=3');
            return [Array.from(params.keys()), Array.from(params.values()),
                    Array.from(params.entries()), params.size];
        })()
    """) == [["a", "b", "a"], ["1", "2", "3"],
             [["a", "1"], ["b", "2"], ["a", "3"]], 3]


def test_a_urls_search_params_write_back_to_the_url(page):
    assert page.evaluate("""
        (() => {
            const url = new URL('https://example.test:8443/a/b?x=1&y=2#frag');
            url.searchParams.append('z', '3');
            url.searchParams.delete('x');
            return [url.href, url.search, url.searchParams.toString(), url.searchParams.size];
        })()
    """) == ["https://example.test:8443/a/b?y=2&z=3#frag", "?y=2&z=3", "y=2&z=3", 2]
