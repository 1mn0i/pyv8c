import pytest

v8n = pytest.importorskip("v8n")
from pyv8c import Browser


def test_evaluate_data_and_handle_boundaries():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("({items: [1, 2]})") == {"items": [1, 2]}
        value = page.evaluate_handle("({items: [1, 2]})")
        assert isinstance(value, v8n.Value)
        assert value.to_python() == {"items": [1, 2]}
        page.close()
        with pytest.raises(RuntimeError):
            value.to_python()
    finally:
        browser.close()
