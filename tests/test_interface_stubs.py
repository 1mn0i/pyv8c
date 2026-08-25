from loguru import logger
import pyv8

from pyv8c import Browser


def test_baseline_only_interface_is_generated_and_keeps_metadata():
    browser = Browser()
    page = browser.new_context().new_page()
    value = page.evaluate("""
      (() => {
        const c = new AbortController();
        return [
          typeof AbortController,
          typeof AbortController.prototype.abort,
          c instanceof AbortController,
          Object.prototype.toString.call(c),
          AbortController.prototype.constructor === AbortController,
          typeof URL.createObjectURL
        ];
      })()
    """)
    assert value == ["function", "function", True, "[object AbortController]", True, "function"]
    browser.close()


def test_stub_method_logs_and_real_member_wins():
    messages = []
    token = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        browser = Browser()
        page = browser.new_context().new_page()
        assert page.evaluate("new AbortController().abort()") is pyv8.Undefined
        assert any("AbortController.abort() 待实现" in message for message in messages)
        # URL is implemented in Python and must not be replaced by a stub.
        assert page.evaluate("new URL('https://example.test/').toString()") == "https://example.test/"
        assert not any("URL.toString() 待实现" in message for message in messages)
        browser.close()
    finally:
        logger.remove(token)


def test_internal_objects_are_not_exposed():
    browser = Browser()
    page = browser.new_context().new_page()
    value = page.evaluate("[typeof Page, typeof Browser, typeof BaselineRegistry, typeof __pyv8c_interface_runtime]")
    assert value == ["undefined", "undefined", "undefined", "undefined"]
    browser.close()
