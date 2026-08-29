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
        assert page.evaluate("AbortSignal.abort()") is pyv8.Undefined
        assert any("AbortController.abort() 待实现" in message for message in messages)
        assert any("AbortSignal.abort() 待实现" in message for message in messages)
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


def test_stub_identity_and_direct_callback_markers_are_separate():
    browser = Browser(stub_logging="off")
    try:
        universe = browser.interface_universe
        abort = universe.class_for("AbortController")
        url = universe.class_for("URL")
        assert abort.__dict__["__v8_idl_stub__"] is True
        assert "__v8_idl_stub__" not in url.__dict__

        abort_config = universe.class_config_for("AbortController")
        abort_item = next(item for item in abort_config["__v8_method__"] if item[0] == "abort")
        abort_callback = getattr(abort_config["__v8_callback_owner__"], abort_item[1])
        assert abort_callback.__v8_direct_callback__ is True
        assert abort_callback.__v8_idl_stub__ is True

        url_config = universe.class_config_for("URL")
        to_string = next(item for item in url_config["__v8_method__"] if item[0] == "toString")
        url_callback = getattr(url_config["__v8_callback_owner__"], to_string[1])
        assert url_callback.__v8_direct_callback__ is True
        assert not hasattr(url_callback, "__v8_idl_stub__")
    finally:
        browser.close()


def test_window_interfaces_are_materialized_on_demand():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        eager = set(page.binding_session.materialized_interfaces)
        assert len(eager) <= 20
        assert "AbortController" not in eager
        assert page.evaluate("typeof AbortController") == "function"
        assert "AbortController" in page.binding_session.materialized_interfaces
        assert page.evaluate("AbortController === AbortController") is True
        assert page.evaluate("Object.getOwnPropertyNames(window).includes('AbortController')") is True
    finally:
        browser.close()
