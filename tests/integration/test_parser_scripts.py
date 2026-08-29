from pyv8c import Browser, Response


def test_parser_blocking_external_script_pauses_and_resumes_parser():
    calls = []

    def loader(url, request):
        calls.append(url)
        return Response("window.__order.push('external:' + document.querySelector('#before').id);", {"url": url, "content-type": "text/javascript"})

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    page.evaluate("window.__order=[]")
    page.set_content("<body><div id='before'></div><script src='/a.js'></script><div id='after'></div></body>", url="http://example.test/")
    assert page.evaluate("[document.querySelector('#before') !== null, document.querySelector('#after') === null, document.readyState]") == [True, True, "loading"]
    page.run_until_idle()
    assert page.evaluate("[document.querySelector('#after') !== null, __order, document.readyState]") == [True, ["external:before"], "complete"]
    assert calls == ["http://example.test/a.js"]
    browser.close()


def test_async_and_defer_order_and_dynamic_script_default_async():
    def loader(url, request):
        if url.endswith("/defer.js"):
            return Response("window.__order.push('defer')", {"url": url, "content-type": "text/javascript"})
        if url.endswith("/async.js"):
            return Response("window.__order.push('async')", {"url": url, "content-type": "text/javascript"})
        return Response("window.__order.push('dynamic')", {"url": url, "content-type": "text/javascript"})

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    page.evaluate("window.__order=[]; window.addEventListener('DOMContentLoaded',()=>__order.push('dcl'))")
    page.set_content("<body><script async src='/async.js'></script><script defer src='/defer.js'></script><div id='end'></div></body>", url="http://example.test/")
    page.evaluate("const s=document.createElement('script'); s.src='/dynamic.js'; document.body.appendChild(s); __order.push('inserted')")
    page.run_until_idle()
    order = page.evaluate("__order")
    assert order.index("defer") < order.index("dcl")
    assert order.index("inserted") < order.index("dynamic")
    browser.close()


def test_detached_dynamic_script_does_not_fetch_until_inserted():
    calls = []
    def loader(url, request):
        calls.append(url)
        return Response("window.__dynamic = (window.__dynamic || 0) + 1", {"url": url, "content-type": "text/javascript"})
    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://example.test/")
    page.evaluate("window.__dynamic=0; const s=document.createElement('script'); s.src='/dynamic.js'; window.__detached=s")
    assert calls == []
    page.evaluate("document.body.appendChild(__detached)")
    page.run_until_idle()
    assert calls == ["http://example.test/dynamic.js"]
    assert page.evaluate("__dynamic") == 1
    browser.close()
