import pytest

pytest.importorskip("v8n")

from pyv8c import Browser


def test_internal_bridge_names_are_not_global_and_url_is_public():
    browser = Browser()
    page = browser.new_context().new_page()
    values = page.evaluate("""
      [
        typeof URL,
        typeof URL.canParse,
        typeof URL.parse,
        typeof URL.createObjectURL,
        typeof URL.revokeObjectURL,
        typeof WindowProperties,
        typeof __pyv8c_createObjectURL,
        typeof __pyv8c_revokeObjectURL,
        Object.getOwnPropertyNames(globalThis).some(name => name.indexOf('__pyv8c_') === 0)
      ]
    """)
    assert values == ["function", "function", "function", "function", "function", "undefined", "undefined", "undefined", False]
    page.close()
    browser.close()


def test_window_aliases_come_from_the_global_template():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    values = page.evaluate("""
      [
        window === self,
        self === globalThis,
        typeof window,
        typeof self,
        typeof globalThis
      ]
    """)
    assert values == [True, True, "object", "object", "object"]
    page.close()
    browser.close()


def test_url_components_and_static_operations():
    browser = Browser()
    page = browser.new_context().new_page()
    values = page.evaluate("""
      (() => {
        const u = new URL('https://user:pass@example.com:8443/a?x=1#h');
        const params = u.searchParams;
        params.set('x', '2');
        return [
          u.href, u.origin, u.protocol, u.username, u.password,
          u.host, u.hostname, u.port, u.pathname, u.search, u.hash,
          params === u.searchParams,
          u.toString(), u.toJSON(), URL.canParse(u.href), URL.parse(u.href).href,
          Object.getPrototypeOf(URL.prototype) === Object.prototype,
          u instanceof URL, Object.prototype.toString.call(u)
        ];
      })()
    """)
    assert values == [
        "https://user:pass@example.com:8443/a?x=2#h", "https://example.com:8443", "https:",
        "user", "pass", "example.com:8443", "example.com", "8443", "/a", "?x=2", "#h",
        True, "https://user:pass@example.com:8443/a?x=2#h", "https://user:pass@example.com:8443/a?x=2#h",
        True, "https://user:pass@example.com:8443/a?x=2#h", True, True, "[object URL]",
    ]
    page.close()
    browser.close()


def test_url_blob_methods_use_context_storage_without_global_bridge():
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="https://github.com/path/index.html")
    values = page.evaluate("""
      (() => {
        const blob = new Blob(['hello'], {type: 'text/plain'});
        const url = URL.createObjectURL(blob);
        const result = [/^blob:https:\\/\\/github\\.com\\/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(url), typeof __pyv8c_createObjectURL];
        URL.revokeObjectURL(url);
        return result;
      })()
    """)
    assert values == [True, "undefined"]
    page.close()
    browser.close()
