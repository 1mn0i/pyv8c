import pytest
from pyv8c import Browser, Response


def page_with(html="<div id='a' class='one two'><span class='one'>x</span><span>y</span></div>"):
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content(html, url="http://example.test/")
    return browser, page


def test_live_collections_and_selector_ast():
    browser, page = page_with()
    assert page.evaluate("[document.querySelector('#a').children.length, document.querySelector('#a').classList.contains('one'), document.querySelector('#a').attributes.length]") == [2, True, 2]
    assert page.evaluate("document.querySelectorAll('div > span:first-of-type').length") == 1
    assert page.evaluate("document.querySelectorAll('span:not(:first-child)').length") == 1
    with pytest.raises(Exception): page.evaluate("document.querySelector('div[')")
    page.close(); browser.close()


def test_event_options_and_history():
    browser, page = page_with()
    assert page.evaluate("(() => { let n=0; const f=()=>n++; document.body.addEventListener('x',f,{once:true}); document.body.dispatchEvent(new Event('x')); document.body.dispatchEvent(new Event('x')); return n; })()") == 1
    assert page.evaluate("(() => { history.pushState({x:1},'', '/next'); return [history.length, history.state.x, location.pathname]; })()") == [2, 1, "/next"]
    page.close(); browser.close()


def test_injected_resource_loader_and_xhr():
    def loader(url, request):
        return Response('{"ok":true}', {"status": 200, "url": url, "headers": {"content-type": "application/json"}})
    browser = Browser(resource_loader=loader); page = browser.new_context().new_page(); page.set_content("<body></body>", url="http://example.test/")
    assert page.evaluate("(() => { const x=new XMLHttpRequest(); x.open('GET','/data'); x.send(); return [x.readyState,x.status,x.responseText]; })()") == [4, 200, '{"ok":true}']
    assert page.evaluate("fetch('/data') instanceof Promise") is True
    page.close(); browser.close()
