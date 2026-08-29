from pyv8c import Browser, Response


def test_static_module_imports_execute_dependency_before_entry_and_block_dcl():
    def loader(url, request):
        if url.endswith("/dep.js"):
            return Response("window.__order.push('dep'); const answer = 41;", {"url": url, "content-type": "text/javascript"})
        if url.endswith("/main.js"):
            return Response("import { answer as value } from './dep.js'; window.__order.push('main:' + value);", {"url": url, "content-type": "text/javascript"})
        return Response("", {"url": url})

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    page.evaluate("window.__order=[]; window.addEventListener('DOMContentLoaded', () => __order.push('dcl'))")
    page.set_content("<body><script type='module' src='/main.js'></script><div id='after'></div></body>", url="http://example.test/")
    # Module fetch/execution is deferred; parsing itself continues.
    assert page.evaluate("document.querySelector('#after') !== null")
    assert page.evaluate("__order") == []
    page.run_until_idle()
    assert page.evaluate("__order") == ["dep", "main:41", "dcl"]
    browser.close()
