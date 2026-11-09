"""Window-owned SameObject values retain JS properties through collection."""
import gc
import weakref

import pytest

from pyv8c import Browser


@pytest.mark.parametrize('name', ['crypto', 'performance', 'customElements'])
def test_window_singleton_expando_survives_gc_without_an_extra_js_reference(name):
    browser = Browser(stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<!doctype html><body></body>', url='https://singleton.test/')
        page.evaluate(f'{name}.probe=()=>42;true')
        page.realm.executor.call(page.realm.v8_context.gc)
        assert page.evaluate(f'typeof {name}.probe') == 'function'
        assert page.evaluate(f'{name}.probe()') == 42
        # A document replacement gets its own singleton, even though the
        # browsing context retains its WindowProxy.
        page.set_content('<!doctype html><body>new</body>', url='https://singleton.test/next')
        assert page.evaluate(f'typeof {name}.probe') == 'undefined'
        page.realm.executor.call(page.realm.v8_context.gc)
    finally:
        browser.close()


def test_closed_pages_release_retained_singleton_wrappers():
    browser = Browser(stub_logging='off')
    profile = browser.new_profile()
    refs = []
    try:
        for _ in range(5):
            page = profile.new_page()
            page.evaluate('crypto.probe=()=>42;performance.probe=crypto;true')
            refs.append(weakref.ref(page))
            page.close()
            assert page.realm._object_roots is None
            assert not page.realm._object_root_ids
            del page
        gc.collect()
        assert not profile.pages
        assert not any(reference() is not None for reference in refs)
    finally:
        browser.close()
