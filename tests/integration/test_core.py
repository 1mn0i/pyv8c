from unittest.mock import patch

import pytest

pytest.importorskip("pyv8")
import pyv8c.platform.browser as browser_runtime
from pyv8c import Browser
from pyv8c.web.dom.selectors import parse


def test_feature_runtime_exposes_baseline_interfaces():
    browser = Browser(stub_logging="off")
    try:
        interfaces = {
            name: browser.interface_universe.spec(name)
            for name in browser.interface_universe.runtime.catalog.interfaces
        }
        assert interfaces["HTMLElement"]["parent"] == "Element"
        assert interfaces["Element"]["parent"] == "Node"
        assert interfaces["Node"]["parent"] == "EventTarget"
    finally:
        browser.close()


def test_browser_trace_is_forwarded_to_page_context():
    with patch.object(browser_runtime.pyv8, "Context", wraps=browser_runtime.pyv8.Context) as context_ctor:
        browser = Browser(trace=True)
        try:
            page = browser.new_context().new_page()
            assert page.trace is True
            assert context_ctor.call_args.kwargs["trace"] is True

            context_ctor.reset_mock()
            overridden = browser.new_context().new_page(trace=False)
            assert overridden.trace is False
            assert context_ctor.call_args.kwargs["trace"] is False
        finally:
            browser.close()


def test_dom_prototype_chain_and_identity():
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content("<!doctype html><html><head></head><body><div id='x'><span>ok</span></div></body></html>")
    values = page.evaluate("""
      (() => { const e = document.querySelector('#x'); return [
        e instanceof HTMLElement, e instanceof Element, e instanceof Node,
        e instanceof EventTarget,
        Object.getPrototypeOf(HTMLElement.prototype) === Element.prototype,
        Object.getPrototypeOf(Element.prototype) === Node.prototype,
        Object.getPrototypeOf(Node.prototype) === EventTarget.prototype,
        document.querySelector('#x') === document.querySelector('#x'),
        Object.prototype.toString.call(e)
      ] })()
    """)
    assert values[:8] == [True] * 8
    assert values[8] == "[object HTMLDivElement]"
    browser.close()


def test_dom_mutation_and_events():
    browser = Browser(); page = browser.new_context().new_page()
    page.set_content("<html><body></body></html>")
    assert page.evaluate("""
      (() => { const d=document.createElement('div'); d.id='a'; document.body.appendChild(d);
        const s=document.createElement('span'); s.textContent='ok'; d.appendChild(s);
        let seen=0; d.addEventListener('x', ()=>seen++); s.dispatchEvent(new Event('x', {bubbles:true}));
        return [document.querySelector('#a span').textContent, seen, d.outerHTML]; })()
    """) == ["ok", 1, '<div id="a"><span>ok</span></div>']
    browser.close()


def test_page_lifecycle_and_bom():
    browser = Browser(); context = browser.new_context(); page = context.new_page()
    page.set_content("<html><body></body></html>", url="https://example.test/path/index.html")
    assert page.evaluate("[window === self, globalThis === window, location.origin, location.pathname, navigator.onLine]") == [True, True, "https://example.test", "/path/index.html", True]
    assert page.evaluate("localStorage.setItem('a', 'b'); localStorage.getItem('a')") == "b"
    page.close()
    with pytest.raises(RuntimeError): page.evaluate("1 + 1")
    context.close(); browser.close()


def test_queue_microtasks_and_selector_parsing_are_reused():
    browser = Browser()
    page = browser.new_context().new_page()
    try:
        page.set_content("<body><div class='item'></div><div class='item'></div></body>")
        page.evaluate("window.__order = []; for (let i = 0; i < 64; i++) queueMicrotask(() => __order.push(i))")
        page.run_until_idle()
        assert page.evaluate("[__order.length, __order[0], __order[63]]") == [64, 0, 63]
        assert page.evaluate("document.querySelectorAll('.item').length") == 2
        assert parse("body > .item") is parse("body > .item")
    finally:
        browser.close()
