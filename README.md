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

Navigate to a URL and download its HTML through the configured network backend:

```python
browser = Browser(real_network=True)
page = browser.new_context().new_page("https://example.com/")
# Equivalent: page = browser.new_context().new_page(); page.goto("https://example.com/")
print(page.evaluate("document.title"))
# Assigning page.url also performs a synchronous Python-side navigation.
page.url = "https://example.com/next"
browser.close()
```

Assigning `location.href` or `window.location` from JavaScript queues the same
navigation. For deterministic or offline use, pass a `resource_loader` instead
of enabling the optional real network backend.

The runtime also exposes public interfaces from the local Chromium baseline.
Interfaces that do not yet have a Python implementation are generated as
runtime stubs; calling one logs a `loguru` info message and returns
`undefined`. Real implementations always override these stubs. Layout,
rendering, and complete Chromium behavior remain out of scope.

```python
browser = Browser(generate_interface_stubs=True, expose_unimplemented=True)
```

The default Chromium interface snapshot is bundled at
`pyv8c/baselines/interface-baseline.json`. A Browser can instead pin a
different complete snapshot:

```python
browser = Browser(interface_baseline_path="baselines/chromium-variant.json")
print(browser.interface_baseline_path)
print(browser.interface_baseline_info["sha256"])
```

The path is resolved when `Browser` is created and its contents are frozen for
that Browser. Multiple Browser instances may use different snapshots in one
process; their generated classes and V8 class-template configurations are
isolated. Window, iframe, and Worker contexts use the snapshot owned by their
Browser.

Regenerate the bundled snapshot from the Chromium source checkout with:

```powershell
python tools/extract_interfaces.py pyv8c\baselines\interface-baseline.json `
  --chromium-root "D:\src\chromium" `
  --build-dir "D:\src\chromium\src\out\Cronet145" `
  --report
```

The extractor scans Chromium-generated Blink binding files under the GN output
directory. If they do not exist, it invokes `autoninja` with target
`blink_generate_bindings_all`; the required Windows toolchain variables are set
from the environment and default to the local checkout paths.

The equivalent explicit toolchain setup is:

```powershell
$env:DEPOT_TOOLS_WIN_TOOLCHAIN = "0"
$env:vs2022_install = "D:\Microsoft\vs\2022\Community"
$env:WINDOWSSDKDIR = "D:\Windows Kits\10"
$env:PATH = "D:\src\depot_tools;" + $env:PATH
```

The bundled baseline uses schema 3 and is the only runtime source for Web IDL
constructor, operation, and attribute-setter signatures. `pyv8c.webidl`
contains only generic signature compilation and value conversion; it has no
interface-specific registration table. Signatures that generated Blink code
cannot express completely are reviewed in `tools/webidl-reviews.json` and
embedded during generation. The review file is never loaded at runtime.

Implemented members must have a generated or reviewed signature. Unresolved
implemented members prevent Browser creation, while unresolved generated stubs
remain callable and log that they are pending implementation. Context-local
callback and constructor adapters keep real Python classes unchanged and allow
different Browser instances to use different baseline versions safely.
