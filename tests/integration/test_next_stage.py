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
    with pytest.raises(BaseException): page.evaluate("document.querySelector('div[')")
    page.close(); browser.close()


def test_form_state_pseudo_classes():
    browser, page = page_with("""
      <form>
        <input id='checked' type='checkbox' checked>
        <input id='unchecked' type='radio'>
        <input id='disabled-input' disabled>
        <button id='enabled-button'>go</button>
        <button id='disabled-button' disabled>stop</button>
        <select><option id='selected' selected>yes</option><option id='other'>no</option></select>
        <select><optgroup disabled><option id='group-disabled'>no</option></optgroup></select>
      </form>
    """)
    assert page.evaluate("[document.querySelectorAll(':checked').length, document.querySelectorAll('input:checked').length, document.querySelectorAll('option:checked').length]") == [2, 1, 1]
    assert page.evaluate("[document.querySelectorAll('input:enabled').length, document.querySelectorAll('button:enabled').length, document.querySelectorAll(':disabled').length]") == [2, 1, 4]
    assert page.evaluate("[document.querySelectorAll('option:enabled').length, document.querySelector('#checked').matches(':checked'), document.querySelector('#disabled-input').matches(':enabled'), document.querySelector('#group-disabled').matches(':disabled')]") == [2, True, False, True]
    with pytest.raises(BaseException):
        page.evaluate("document.querySelector('input:checked(foo)')")
    page.close(); browser.close()


def test_invalid_selector_is_native_syntax_error():
    browser, page = page_with()
    assert page.evaluate("""
      (() => {
        try { document.querySelector('*,:x'); }
        catch (error) { return [error.name, error instanceof SyntaxError]; }
        return ['missing', false];
      })()
    """) == ["SyntaxError", True]
    assert page.tasks.drain_task_errors() == ()
    page.close(); browser.close()


def test_window_top_resolves_to_the_outermost_frame():
    browser = Browser(resource_loader=lambda url, request: {"body": "<body>frame</body>", "url": url})
    page = browser.new_context().new_page()
    page.set_content("<iframe src='/frame.html'></iframe>", url="http://example.test/")
    page.run_until_idle(timeout=1)
    frame_page = next(iter(page._owned.frames))
    assert page.evaluate("window.top === window") is True
    assert page.evaluate("document.querySelector('iframe').contentWindow.top === window") is True
    assert frame_page.evaluate("window.top === window") is False
    assert frame_page.evaluate("window.top === window.parent") is True
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
    assert page.evaluate("(() => { window.__xhr=new XMLHttpRequest(); __xhr.open('GET','/data'); __xhr.send(); return [__xhr.readyState,__xhr.status,__xhr.responseText]; })()") == [1, 0, '']
    page.run_until_idle()
    assert page.evaluate("[__xhr.readyState,__xhr.status,__xhr.responseText]") == [4, 200, '{"ok":true}']
    assert page.evaluate("fetch('/data') instanceof Promise") is True
    page.close(); browser.close()
