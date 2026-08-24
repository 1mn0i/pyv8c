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

The first release intentionally focuses on deterministic BOM/DOM behavior and
does not implement network loading, layout, rendering, workers, or iframes.
