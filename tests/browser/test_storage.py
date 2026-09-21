"""Storage: the entries, their coercion, and the names they are also reachable by."""
import pytest
import pyv8

from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(stub_logging="off")
    instance = browser.new_context().new_page()
    instance.set_content("<body></body>", url="http://storage.test/")
    try:
        yield instance
    finally:
        browser.close()


def test_storage_round_trips_forgets_and_reports_its_length(page):
    assert page.evaluate("""
        (() => {
            localStorage.setItem('a', '1');
            localStorage.setItem('b', '2');
            const keys = [localStorage.key(0), localStorage.key(1), localStorage.length];
            localStorage.removeItem('a');
            return [localStorage.getItem('a'), localStorage.getItem('b'), keys,
                    localStorage.length, localStorage.key(5)];
        })()
    """) == [pyv8.Null, "2", ["a", "b", 2], 1, pyv8.Null]


def test_a_stored_name_is_also_a_property(page):
    assert page.evaluate("""
        (() => {
            localStorage.token = 'v';
            const read = [localStorage.token, localStorage.getItem('token'),
                          'token' in localStorage, Object.keys(localStorage)];
            delete localStorage.token;
            return [read, [localStorage.token, localStorage.getItem('token')],
                    localStorage.getItem === undefined ? 'shadowed' : 'kept'];
        })()
    """) == [["v", "v", True, ["token"]], [pyv8.Undefined, pyv8.Null], "kept"]


def test_storage_coerces_keys_and_values_to_strings(page):
    assert page.evaluate("""
        (() => {
            localStorage.setItem(7, 42);
            sessionStorage.setItem('a', '1');
            return [localStorage.getItem('7'), localStorage.getItem(7),
                    localStorage.length, sessionStorage.getItem('a'),
                    localStorage.getItem('missing')];
        })()
    """) == ["42", "42", 1, "1", pyv8.Null]
