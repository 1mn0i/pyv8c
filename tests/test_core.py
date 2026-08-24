import pytest

pytest.importorskip("pyv8")
from pyv8c import Browser, INTERFACES


def test_interface_registry_is_explicit():
    assert INTERFACES["HTMLElement"].parent == "Element"
    assert INTERFACES["Element"].parent == "Node"
    assert INTERFACES["Node"].parent == "EventTarget"


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
