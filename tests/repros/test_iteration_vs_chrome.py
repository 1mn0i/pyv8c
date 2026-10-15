"""Serial local iterable comparison using the explicit migrated baseline."""
import pytest
from harness import chrome_reference, ReferenceUnavailable
from pyv8c import Browser
from pathlib import Path
from runpy import run_path
_cases = run_path(str(Path(__file__).resolve().parents[1] / "browser" / "test_iteration.py"))
CASES, IDL = _cases["CASES"], _cases["IDL"]

@pytest.fixture(scope='module')
def reference():
    try:
        with chrome_reference() as session: yield session
    except ReferenceUnavailable as exc: pytest.skip(str(exc))

@pytest.mark.parametrize('name,script', CASES)
def test_iteration_matches_chrome(reference,name,script):
    chrome=reference.browser.new_page()
    browser=Browser(idl_path=IDL, real_network=False, stub_logging='off')
    try:
        chrome.route('https://iteration.test/**',lambda route: route.fulfill(status=200,content_type='text/html',body='<body></body>'))
        chrome.goto('https://iteration.test/')
        page=browser.new_profile().new_page();page.set_content('<body></body>',url='https://iteration.test/')
        assert page.evaluate('('+script+')()') == chrome.evaluate(script)
    finally:
        browser.close();chrome.close()
