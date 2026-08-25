from pyv8c import Browser


def _page(html="<body></body>"):
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content(html, url="https://example.test/")
    return browser, page


def test_required_arguments_and_interface_arguments_are_type_errors():
    browser, page = _page("<body><div></div></body>")
    value = page.evaluate("""(() => {
      const out = [];
      for (const fn of [
        () => document.createElement(),
        () => document.body.querySelector(),
        () => document.body.appendChild(1),
        () => document.body.dispatchEvent({}),
      ]) {
        try { fn(); out.push(false); } catch (e) { out.push([e.name, e instanceof TypeError]); }
      }
      return out;
    })()""")
    assert value == [["TypeError", True]] * 4
    browser.close()


def test_variadic_optional_and_dictionary_arguments():
    browser, page = _page()
    value = page.evaluate("""(() => {
      const list = document.body.classList;
      let missing;
      try { list.add(); } catch (e) { missing = [e.name, e instanceof TypeError]; }
      list.add('one', 'two');
      document.body.addEventListener('x', () => {}, {once: true, capture: false});
      return [missing, list.value];
    })()""")
    assert value == [["TypeError", True], "one two"]
    browser.close()


def test_null_undefined_and_webidl_coercion():
    browser, page = _page()
    value = page.evaluate("""(() => {
      const event = new Event('x', undefined);
      const params = new URLSearchParams(null);
      const blob = new Blob(undefined);
      return [event.type, event.bubbles, params.toString(), blob.size,
              document.body.classList.toggle('x', undefined)];
    })()""")
    assert value == ["x", False, "null=", 0, True]
    browser.close()


def test_constructor_length_and_url_argument_validation():
    browser, page = _page()
    value = page.evaluate("""(() => {
      let invalid;
      try { new URL(); } catch (e) { invalid = [e.name, e instanceof TypeError]; }
      const u = new URL('https://example.test/a');
      return [invalid, URL.length, u.searchParams === u.searchParams];
    })()""")
    assert value[0][0] in ("TypeError", "URIError")
    assert value[0][1] is True
    assert value[1:] == [1, True]
    browser.close()


def test_attribute_setter_enum_and_numeric_validation():
    browser, page = _page()
    value = page.evaluate("""(() => {
      const out = [];
      const ws = Object.create(WebSocket.prototype);
      try { ws.binaryType = 'invalid'; } catch (e) { out.push([e.name, e instanceof TypeError]); }
      document.body.classList.value = 7;
      out.push(document.body.classList.value);
      return out;
    })()""")
    assert value == [["TypeError", True], "7"]
    browser.close()


def test_fetch_argument_failures_are_rejected_promises():
    browser, page = _page()
    page.evaluate("fetch().catch(e => window.__fetch_error = [e.name, e instanceof TypeError])")
    page.run_until_idle()
    assert page.evaluate("__fetch_error") == ["TypeError", True]
    browser.close()
