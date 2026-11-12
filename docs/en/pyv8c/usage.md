# Usage Guide

[简体中文](../../pyv8c/usage.md) | **English**

[Documentation Index](../README.md)

## Creation and Shutdown

`Browser` manages shared configuration, `Profile` manages session state, and `Page` manages pages. Cookies and in-memory state are independent across Profiles; a single Profile can create multiple pages.

```python
from pyv8c import Browser

browser = Browser(real_network=False)
try:
    profile = browser.new_profile()
    page = profile.new_page()
    page.set_content("<p id='text'>内容</p>", url="https://example.test/")
    assert page.evaluate("document.querySelector('#text').textContent") == "内容"
finally:
    browser.close()
```

`new_page()` defaults to `about:blank`. Use `goto()` to load the main document; do not conflate creating a Page with completing network navigation.

`page.close()`, `profile.close()`, and `browser.close()` release owner resources level by level; a normal shutdown can be called repeatedly. If cleanup of an independent resource fails, an exception is reported while other resources are still released on a best-effort basis; an exception must not be treated as a successful shutdown. Closing a child page does not stop an execution group still used by other pages.

Closing from another thread can interrupt active JS. A background task terminated by a matching shutdown ticket does not report a host failure; a host waiting on a synchronous operation still receives `pyv8.JavaScriptTerminated`. This exception inherits from `BaseException` and is not an ordinary JS exception. Catch it explicitly when classification is needed; real timeouts and unknown terminations must not be uniformly ignored.

## Page APIs

| Call | Contract |
| --- | --- |
| `set_content(html, url="about:blank")` | Provides HTML and starts document processing; completion of external resources requires a separate wait |
| `goto(url, timeout=30, wait_until="load")` | Navigates and waits for `commit`, `domcontentloaded`, or `load`; `None` is treated as the commit phase |
| `evaluate(script, filename=None, timeout=30)` | Executes source code and materializes returnable data; does not automatically await Promises |
| `evaluate_handle(script, filename=None)` | Returns a primitive value or a live JS handle; does not perform data snapshot conversion |
| `wait_for(expression, timeout=30)` | Waits for a JS condition to become true; the condition should have no side effects |
| `wait_for_load_state(state="load", timeout=30)` | Waits for the load state of the current document generation; fails if the document is replaced or closed |
| `run_until_idle(timeout=1.0)` | Waits for the target Agent's runnable work and checkpoints to drain; returns True on success and False on timeout |
| `dispatch_event(event_type, ...)` | Dispatches a host-constructed event to the specified window, document, or element |
| `set_visibility(hidden)` | Updates visibility and drives related lifecycle behavior |

`goto(..., wait_until="domcontentloaded")` may return while `load` listeners are still running. Network request completion, DOM readiness, and application readiness are different conditions and should be awaited separately. When the loading flow is terminated, waiting for `load` fails; an already-started dispatch is not treated as a successful load.

```python
page.set_content("<script>globalThis.ready = false; setTimeout(() => ready = true, 0)</script>")
page.wait_for_load_state("load", timeout=2)
page.wait_for("ready === true", timeout=2)
```

`run_until_idle(timeout=0)` only queries state. Far-future timers, in-flight network activity, long-lived connections, and live Workers do not directly block idleness; do not use idle waiting as a substitute for waiting on business conditions. The owner thread must not synchronously wait on unfinished navigation or network operations.

## Return Values and Handles

`evaluate()` converts objects and arrays into materializable Python data. When object identity such as DOM nodes or functions is required, use `evaluate_handle()`; operations must return to the owner thread of the owning execution group:

```python
handle = page.evaluate_handle("({answer: 42})")
snapshot = page.executor.call(handle.to_python)
assert snapshot == {"answer": 42}
```

`page.executor` is an internal execution facility, used only when native handle operations are needed. Do not read handles directly from other threads, and do not pass live objects between unrelated pages. After navigation replaces the Context, an old handle is no longer an object of the new document; the handle becomes invalid once the original Context is finally closed.

## Environment Configuration

`BrowserEnvironment` uses flat key names and is immutable after construction. Values not explicitly set come from the bundled environment profile and derived rules. `Browser(environment=...)` sets the default environment, and `new_page(environment=...)` can override the page environment.

```python
from pyv8c import BrowserEnvironment

environment = BrowserEnvironment({
    "languages": ["zh-CN", "zh"],
    "viewport.width": 1280,
    "viewport.height": 720,
    "viewport.device_scale_factor": 1,
})
values = environment.resolved()
assert values["viewport.width"] == 1280
```

Common configuration includes `user_agent`, `platform`, `language`, `languages`, `timezone`, `webdriver`, `hardware_concurrency`, `device_memory`, as well as `viewport.*`, `screen.*`, `client_hints.*`, `media.*`, `permissions.*`, and others. Not every string is valid; unknown keys and invalid values are rejected during validation.

`to_dict()` returns the explicit configuration, and `resolved()` returns the fully resolved values; `overlay()` creates a new environment with overrides applied, while `from_file()` and `to_file()` work with JSON files. Changing environment values does not automatically provide real hardware, permissions, or media decoding capabilities.

`form_controls.submit_label` and `form_controls.reset_label` configure the browser UI text for submit and reset buttons when `value` is not explicitly specified, and are used to compute button dimensions. This text is determined by the browser UI language and may differ from `navigator.language`; the bundled profile uses the Chinese labels from local Chrome. They do not change the `value` attribute of input elements.

`form_controls.file_button_label` and `form_controls.file_empty_label` are used to estimate the dimensions of the file-selection control's button and the placeholder text shown when no file is selected; they should also be configured according to the browser UI language.

`form_controls.time_font_family` specifies the actual font family used by the time control's default `monospace` font, for internal field dimension calculation; other fonts explicitly set by the page are not affected by this option.
The current time control estimates internal field dimensions using a 24-hour clock; other UI formats, custom fonts, and control pseudo-element styles are not yet guaranteed to match Chrome exactly.

## Browser Options

| Option | Default value and meaning |
| --- | --- |
| `real_network` | True; whether to install the real network engine |
| `http_engine` / `ws_engine` | `curl_cffi` / `websockets`; engine name, instance, factory, or None |
| `http_options` / `ws_options` | Per-engine configuration, validated when a Profile is created |
| `proxy` | None; direct connection when unset, overridable by Profile |
| `default_headers` | Default headers for host `page.net` requests |
| `resource_loader` / `resource_interceptor` | None; resource source and interception callbacks |
| `threaded_loaders` | False; whether to place host callbacks in a bounded thread pool |
| `cache` / `cache_dir` | True / None; a persistent resource response cache exists only when a directory is specified |
| `frame_rate` | 60.0; virtual frame clock frequency of the page tree, must be a finite positive number |
| `trace` / `traceable` / `trace_callback` / `trace_scripts` | False / False / None / None; tracing configuration and script URL filtering |
| `expose_unimplemented` | True; whether to expose unimplemented interfaces in the baseline |
| `stub_logging` | `off`; can also be `on` or `verbose`, controls logging of unimplemented members |
| `interface_overrides` | None; experimental replacement callbacks for interface members |
| `idl_path` | None; by default reads the in-package `pyv8c/pyv8c/idl/idl.zip` |
| `baseline_mismatch` | `warn`; warns when the implementation or Window order does not match the selected baseline, `error` enables strict validation |

`new_profile(proxy=..., trace=..., traceable=..., trace_callback=..., trace_scripts=...)` can set the corresponding session options; `new_page(trace_scripts=...)` can also override script filtering, see [Runtime Tracing](tracing.md). For thread restrictions on network and resource callbacks, see [Resource Loading](resource-loading.md).

The format of `interface_overrides` is `{"接口名": {"成员名": 回调}}`; a property can use `{"get": 回调, "set": 回调}`. Instance methods receive `(receiver, *args)`, static methods receive `(*args)`, property reads receive `(receiver)`, and writes receive `(receiver, value)`. Overrides for unknown members, illegal slots, and writes to read-only properties are rejected; constructors, constants, and named/indexed handlers are not overridden. Official fixes should go into the owning implementation layer.

## Network Calls

`page.net.get()`, `request()`, and their corresponding async entry points are host transport requests that use the Profile network backend and cookies; they do not execute the page's `fetch`, nor do they go through the full page resource policy. The async entry points return a Future.

`page.net.fetch()`, `xhr()`, and their async entry points call the page's current Fetch/XMLHttpRequest, whose implementations may have been replaced by the page; the result is a host response snapshot subject to the page resource pipeline and the corresponding same-origin rules. Do not treat the two groups of calls as interchangeable entry points.

## Events and Input

```python
page.set_content("<button id='go'>继续</button>")
page.evaluate("document.querySelector('#go').addEventListener('click', () => globalThis.clicked = true)")
page.click("#go")
page.wait_for("globalThis.clicked === true", timeout=1)
```

The host supports `click()`, `dblclick()`, `hover()`, and `page.mouse`. Input behavior is based on current layout and hit testing; geometry and real Chrome rendering may still differ. `dispatch_event()` provides explicit event dispatch and is not equivalent to a full input action sequence.

Application-side exceptions, host failures, network failures, and execution termination are different outcomes. When troubleshooting, first inspect the failing stage, then choose [Runtime Tracing](tracing.md), the [Debugger](devtools.md), or local Chrome comparison; do not mask problems by extending fixed sleeps.
