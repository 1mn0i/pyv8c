import pytest

pyv8 = pytest.importorskip("pyv8")
from pyv8c import Browser


def test_client_information_aliases_navigator():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("[window.clientInformation === window.navigator, clientInformation === navigator, clientInformation.userAgent === navigator.userAgent]") == [True, True, True]
    finally:
        browser.close()
