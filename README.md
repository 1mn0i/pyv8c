# pyv8c

`pyv8c` is a small, deterministic Chromium-like BOM/DOM runtime built on the
local `pyv8` extension. Its DOM tree is native Python objects, not parser-node
wrappers. Interface metadata is explicit and Python implementation classes are
registered with their Web IDL parent so pyv8 can expose the corresponding JS
prototype chain.

```python
from pyv8c import Browser

browser = Browser()
page = browser.new_context().new_page()
page.set_content("<html><body><div id='app'>ready</div></body></html>")
print(page.evaluate("document.querySelector('#app').textContent"))
browser.close()
```

The runtime also exposes public interfaces from the local Chromium baseline.
Interfaces that do not yet have a Python implementation are generated as
runtime stubs; calling one logs a `loguru` info message and returns
`undefined`. Real implementations always override these stubs. Layout,
rendering, and complete Chromium behavior remain out of scope.

```python
browser = Browser(generate_interface_stubs=True, expose_unimplemented=True)
```
