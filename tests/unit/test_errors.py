import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c import Browser, V8TypeError


def test_dom_exception_constructor_and_legacy_code():
    browser = Browser(); page = browser.new_context().new_page()
    value = page.evaluate("(() => { const e = new DOMException('bad', 'InvalidStateError'); return [e.name, e.message, e.code, e instanceof DOMException, Object.prototype.toString.call(e), DOMException.length]; })()")
    assert value == ["InvalidStateError", "bad", 11, True, "[object DOMException]", 0]
    browser.close()


def test_python_v8_error_metadata_and_dom_errors():
    browser = Browser(); page = browser.new_context().new_page(); page.set_content("<body><div></div></body>")
    value = page.evaluate("""
      (() => {
        const results = [];
        try { document.body.appendChild(1); } catch (e) { results.push([e.name, e.message, e instanceof TypeError]); }
        try { document.body.removeChild(document.createElement('span')); } catch (e) { results.push([e.name, e.code, e instanceof DOMException]); }
        try { document.querySelector('div['); } catch (e) { results.push([e.name, e instanceof SyntaxError]); }
        return results;
      })()
    """)
    assert value[0][0:3] == [
        "TypeError",
        "Failed to execute 'appendChild' on 'Node': parameter 1 is not of type 'Node'.",
        True,
    ]
    assert value[1] == ["NotFoundError", 8, True]
    assert value[2] == ["SyntaxError", True]
    browser.close()


def test_history_security_error_and_fetch_rejection_object():
    browser = Browser(resource_loader=lambda url, request: (_ for _ in ()).throw(RuntimeError("offline")))
    page = browser.new_context().new_page(); page.set_content("<body></body>", url="https://example.test/")
    assert page.evaluate("(() => { try { history.pushState({}, '', 'https://other.test/'); } catch (e) { return [e.name, e.code, e instanceof DOMException]; } })()") == ["SecurityError", 18, True]
    page.evaluate("fetch('/offline').catch(e => window.__fetchError = [e.name, e instanceof Error, e.message])")
    page.run_until_idle()
    assert page.evaluate("__fetchError.slice(0, 2)") == ["Error", True]
    browser.close()


def test_a_dom_exception_reaches_python_as_the_browser_stringifies_it():
    browser = Browser(); page = browser.new_context().new_page()
    value = page.evaluate(
        "e = new DOMException('Service workers are not implemented here.',"
        " 'NotSupportedError'); e")
    assert str(value) == "NotSupportedError: Service workers are not implemented here."
    assert str(page.evaluate("new DOMException('m')")) == "Error: m"
    assert str(page.evaluate("new DOMException('m', '')")) == "m"
    assert str(page.evaluate("new DOMException()")) == "Error"
    browser.close()


def test_error_classes_are_v8_bridge_exceptions():
    assert V8TypeError("boom").__v8_error__ is True
    with pytest.raises(V8TypeError):
        raise V8TypeError("boom")
