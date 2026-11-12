# Network Transport

[简体中文](../../pyv8c/network-transports.md) | **English**

[Documentation Index](../README.md)

## Transport Roles

HTTP and WebSocket are two independently replaceable roles. By default HTTP is `curl_cffi` and WebSocket is `websockets`; `cronet` is optional and uses the cycronet dependency.

```powershell
python -m pip install -e .
# Install separately when cycronet is needed:
python -m pip install -e ".[cronet]"
```

Browser enables real networking by default; `real_network=False` does not install transports and is used for local resource testing. A missing dependency for the selected engine fails explicitly rather than silently switching to another engine.

```python
from pyv8c import Browser

browser = Browser(http_engine="curl_cffi", ws_engine="websockets")
try:
    page = browser.new_profile().new_page()
    # response = page.net.get("https://example.com/")
finally:
    browser.close()
```

`http_engine` and `ws_engine` can be a registered name, an object conforming to the protocol, a factory created once per Profile, or None. None means the corresponding role is missing; it does not mean all loaders and local resources are disabled.

## Arguments and Proxies

`http_options` and `ws_options` are passed to the corresponding engine. Built-in engines build validation from the keys accepted by themselves and the underlying client; misspelled options or options passed to the wrong role fail when the Profile is created. The supported set depends on the currently installed version, and you should not copy another engine's arguments over directly.

Common curl_cffi arguments include `impersonate`, `verify`, and `timeout`; the framework also handles `default_headers`, `max_workers`, and the unified `proxy`. `impersonate` affects only the selected transport and does not synchronously change the IDL, navigator, device, or page environment.

The Browser's `proxy` is the unified HTTP/HTTPS proxy entry point and can be overridden by a Profile; unset or an empty string means a direct connection and does not automatically inherit the system proxy. Proxy credentials in the URL are supported; the unified proxy model does not support SOCKS. An engine must not declare proxy support yet silently ignore the configuration.

## Host Requests and Page Requests

| Entry point | Behavior |
| --- | --- |
| `page.net.get(url, ...)`、`request(method, url, ...)` | Host network request; does not execute page Fetch and does not fully apply page resource policy |
| `page.net.get_async(...)`、`request_async(...)` | Asynchronous host request, returns a Future |
| `page.net.fetch(...)`、`xhr(...)` | Calls the page's current Fetch/XMLHttpRequest and waits for a host response snapshot |
| `page.net.fetch_async(...)`、`xhr_async(...)` | The Future of the corresponding page call |

Common keywords for host requests include headers, params, data, json, cookies, timeout, referer, and options. Host and page requests share Profile Cookie state, but differ in security policy, interceptors, caching, and page override behavior.

NetworkRequest is constructed with keywords and contains URL, method, headers, body, timeout, credentials, mode, redirect, origin, site_for_cookies, top_level_navigation, and options. NetworkResponse is ordinary response data providing read entry points such as status, headers, body, text, content, and json(); it is not a Web Response.

A Profile always owns a CookieJar. The transport decides Cookie sending and saving based on request credentials; access to document.cookie is additionally restricted by page rules. A ready-made transport instance can be owned by only one active Profile; use a factory or a registered transport name when you need multiple Profiles.

Cookie Domain is checked with a Public Suffix List snapshot built into the dependency; `SameSite=None` requires `Secure`, and some attributes of `__Secure-` / `__Host-` are validated. Page requests filter Strict, Lax, and default SameSite Cookies by initiating site, ancestor sites, and the top-level navigation method; host requests do not automatically apply the page site context. Partitioned Cookies and the browser's full Cookie policy are not yet implemented, so the CookieJar must not be treated as a Chrome security boundary.

A cross-origin CORS request, when required, first sends a credential-less OPTIONS preflight and validates the origin, credentials, method, and request headers; if the preflight fails, the actual request is not sent. Preflight results are not currently cached; a transport that cannot control redirects per hop rejects requests requiring a preflight. Streaming request bodies are not yet implemented, and using a `ReadableStream` as the request body errors before sending; forced preflight triggered by XHR upload listeners and private network access policy are not yet implemented, so the host should restrict network access before loading untrusted pages.

## Custom Transports

HTTP implements `request_sync(request)`, `request_async(tasks, request, callback)`, and `close()`, and connects to the framework CookieJar. The asynchronous callback has the form `(response, error)` and delivers only ordinary data; the resource layer is responsible for queueing back to the owner, and a transport cannot modify the DOM directly.

WebSocket implements `websocket(tasks, url, protocols, callbacks, options=None)`, `close_websocket(socket)`, and `close()`. The role protocols are the public HttpTransport and WebSocketTransport respectively.

```python
from pyv8c import register_transport

# MyHttpTransport must implement the full transport protocol.
def install_transport(MyHttpTransport):
    register_transport(
        "local-http",
        lambda options=None: MyHttpTransport(options=options),
        option_keys=lambda: frozenset({"timeout"}),
    )
```

A registered factory is called with `options=<dict or None>`; an unregistered factory passed directly to Browser is called with no arguments. Existing instances and unregistered factories can no longer be configured through http_options/ws_options or the unified proxy, so configuration should be completed when the instance is created. `register_ws_transport()` registers the WebSocket role; `proxy=True` at registration means the unified proxy is actually supported. The asynchronous cancellation handle belongs to the transport layer and divides responsibilities with the page resource LoadHandle, and owner validation of late results must not be omitted.

The resource layer generally requires the engine to return a single-hop response, advanced by the shared redirects rules; an engine that cannot disable automatic redirects must explicitly declare that behavior and must not silently swallow intermediate hops.

## WebSockets and Limits

WebSocket open, message, error, and close are posted back to the networking source. The default websockets sender sends asynchronously in FIFO order, and decreases in bufferedAmount are posted back to the owner; page destruction cancels the unfinished work belonging to that connection. A normal socket.close and the abort policy on page destruction are different.

The default transport provides cancellation and shutdown coordination for Windows blocking sockets, but a custom connection factory needs to provide equivalent release capability. Full backpressure, queue budgets, blocking before the connection object is returned, and each engine's lifecycle all need separate verification.

TLS, HTTP version, compression, proxy, and handshake permutations depend on the engine and dependency build; choosing a particular engine does not mean parity with Chrome's full network stack. For specific request header rules see [Request Headers](request-headers.md).
