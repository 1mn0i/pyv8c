import pytest

from pyv8c import Browser, Response
from pyv8c.bindings.universe import BaselineRepository, InterfaceUniverse
from pyv8c.core.errors import DOMException
from pyv8c.workers.clone import structured_clone
from pyv8c.network.backend import CookieJar


def new_page(*, loader=None):
    browser = Browser(resource_loader=loader)
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<body></body>", url="http://example.test/root")
    page.run_until_idle()
    return browser, context, page


def test_closed_context_rejects_new_pages_and_blob_access():
    browser = Browser()
    context = browser.new_context()
    context.close()
    with pytest.raises(DOMException, match="BrowserContext is closed"):
        context.new_page()
    with pytest.raises(DOMException, match="BrowserContext is closed"):
        context.resolve_blob_url("blob:null/not-present")
    browser.close()


def test_binding_sessions_are_isolated_per_realm():
    universe = InterfaceUniverse(BaselineRepository.load(), stub_logging="off")
    first_realm = object()
    second_realm = object()
    first = universe.binding_session(first_realm)
    second = universe.binding_session(second_realm)
    assert first is not second
    first_config = first.class_config_for("AbortController")
    second_config = second.class_config_for("AbortController")
    assert first_config is not second_config
    assert first_config["__v8_callback_owner__"] is not second_config["__v8_callback_owner__"]
    assert first.realm is first_realm
    assert second.realm is second_realm


def test_timers_respect_delay_repeat_and_cancellation():
    browser, _, page = new_page()
    page.evaluate("window.__timerCount=0; window.__timer=setTimeout(()=>__timerCount++, 25)")
    page.run_until_idle()
    assert page.evaluate("__timerCount") == 0
    page.run_until_idle(timeout=0.25)
    assert page.evaluate("__timerCount") == 1

    page.evaluate("window.__ticks=0; window.__interval=setInterval(()=>{ if (++__ticks === 2) clearInterval(__interval); }, 10)")
    page.run_until_idle(timeout=0.25)
    assert page.evaluate("__ticks") == 2
    page.close()
    browser.close()


def test_reparent_and_replacement_keep_tree_identity_consistent():
    browser, _, page = new_page()
    assert page.evaluate("""(() => {
        const a=document.createElement('div'), b=document.createElement('div');
        const old=document.createElement('i'), replacement=document.createElement('b');
        a.appendChild(old); b.appendChild(replacement); a.replaceChild(replacement, old);
        const moved=a.firstChild; b.innerHTML='<span>fresh</span>';
        return [old.parentNode === null, moved.parentNode === a,
                a.childNodes.length, b.childNodes.length,
                b.firstChild.parentNode === b];
    })()""") == [True, True, 1, 1, True]
    assert page.evaluate("""(() => {
        const host=document.createElement('div'), child=document.createElement('span');
        host.appendChild(child); host.textContent='text';
        return [child.parentNode === null, host.childNodes.length, host.firstChild.parentNode === host];
    })()""") == [True, 1, True]
    page.close()
    browser.close()


def test_fixture_resource_callbacks_are_always_asynchronous():
    def loader(url, request):
        return Response("fixture", {"url": url})

    browser, _, page = new_page(loader=loader)
    assert page.evaluate("""(() => {
        window.__order=[];
        fetch('/data').then(() => __order.push('fetch'));
        __order.push('after');
        return __order.slice();
    })()""") == ["after"]
    page.run_until_idle()
    assert page.evaluate("__order") == ["after", "fetch"]
    page.close()
    browser.close()


def test_structured_clone_rejects_cycles_and_nontransferable_values():
    cyclic = []
    cyclic.append(cyclic)
    for value in (cyclic, lambda: None, object()):
        with pytest.raises(DOMException) as caught:
            structured_clone(value)
        assert caught.value.get_name() == "DataCloneError"


def test_js_values_are_strictly_cloneable_for_worker_messages():
    browser, _, page = new_page()
    value = page.evaluate("({value:4, bytes:new Uint8Array([1,2,3])})")
    cloned = structured_clone(value)
    assert cloned["value"] == 4
    assert bytes(cloned["bytes"]) == b"\x01\x02\x03"
    page.close()
    browser.close()


def test_cookie_path_match_uses_path_boundary_and_preserves_repeated_set_cookie():
    jar = CookieJar()
    jar.add_from_response("http://example.test/foo/start", ["first=one; Path=/foo", "root=yes; Path=/"])
    assert jar.header_for("http://example.test/foo/next") == "first=one; root=yes"
    assert jar.header_for("http://example.test/foobar") == "root=yes"
