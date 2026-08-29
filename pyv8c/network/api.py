import queue
import threading
from urllib.parse import urljoin

import pyv8
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ..dom.model import EventTarget, Event
from ..bindings.registry import interface, js_attributes, js_methods
from .backend import NetworkRequest, NetworkUnavailableError
from ..core.errors import (
    V8TypeError, V8SyntaxError, InvalidStateError, NotSupportedError, NetworkError,
    DataCloneError, as_js_error,
)
from ..workers.clone import structured_clone


def _decode_clone_markers(value):
    if isinstance(value, dict) and set(value) == {"__pyv8c_arraybuffer"}:
        return bytes(int(item) & 255 for item in value["__pyv8c_arraybuffer"])
    if isinstance(value, dict): return {key: _decode_clone_markers(item) for key, item in value.items()}
    if isinstance(value, list): return [_decode_clone_markers(item) for item in value]
    return value


def _bytes_from_value(value):
    if isinstance(value, Blob): return value._data
    if isinstance(value, bytes): return value
    if isinstance(value, bytearray): return bytes(value)
    if isinstance(value, memoryview): return value.tobytes()
    if isinstance(value, str): return value.encode("utf-8")
    if isinstance(value, (list, tuple)): return bytes(int(item) & 255 for item in value)
    try:
        return bytes(value)
    except (TypeError, ValueError):
        return None


@interface("Headers", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Headers:
    __v8_method__ = js_methods(("get", "fn_get", 1, 1), ("set", "fn_set", 2, 0), ("append", "fn_append", 2, 0), ("delete", "fn_delete", 1, 0), ("has", "fn_has", 1, 1))
    def __init__(self, init=None, **_):
        self._data = {}
        if isinstance(init, Headers): self._data.update(init._data)
        elif isinstance(init, dict):
            for key, value in init.items(): self.fn_set(key, value)
    def fn_get(self, key): return self._data.get(str(key).lower(), pyv8.Null)
    def fn_set(self, key, value): self._data[str(key).lower()] = str(value)
    def fn_append(self, key, value):
        key = str(key).lower(); self._data[key] = self._data.get(key, "") + (", " if key in self._data else "") + str(value)
    def fn_delete(self, key): self._data.pop(str(key).lower(), None)
    def fn_has(self, key): return str(key).lower() in self._data
    def items(self): return self._data.items()


@interface("Blob", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Blob:
    __v8_attribute__ = js_attributes(("size", "get_size", None, 0, 1), ("type", "get_type", None, 0, 1))
    __v8_method__ = js_methods(("slice", "fn_slice", 3, 1), ("text", "fn_text", 0, 1), ("arrayBuffer", "fn_arrayBuffer", 0, 1))
    def __init__(self, parts=None, options=None, _realm=None, **_):
        self._realm = _realm
        parts = parts if isinstance(parts, (list, tuple)) else ([] if parts in (None, pyv8.Null, pyv8.Undefined) else [parts])
        data = bytearray()
        for part in parts:
            chunk = _bytes_from_value(part)
            if chunk is not None: data.extend(chunk)
        options = options if isinstance(options, dict) else {}
        self._data = bytes(data); self._type = str(options.get("type", "")).lower()
        if any(ord(ch) < 0x20 or ord(ch) > 0x7e for ch in self._type): self._type = ""
    def get_size(self): return len(self._data)
    def get_type(self): return self._type
    def fn_slice(self, start=0, end=pyv8.Undefined, content_type=""):
        size = len(self._data); start = int(start); end = size if end is pyv8.Undefined else int(end)
        start = max(size + start, 0) if start < 0 else min(start, size); end = max(size + end, 0) if end < 0 else min(end, size)
        return Blob([self._data[min(start, end):max(start, end)]], {"type": str(content_type).lower()}, _realm=self._realm)
    def fn_text(self):
        value = self._data.decode("utf-8", "replace"); return self._realm.promise_resolve(value) if self._realm else value
    def fn_arrayBuffer(self):
        return self._realm.promise_resolve(self._data) if self._realm else self._data


@interface("Request", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Request:
    __v8_attribute__ = js_attributes(
        ("url", "get_url", None, 0, 1), ("method", "get_method", None, 0, 1),
        ("headers", "get_headers", None, 0, 1), ("mode", "get_mode", None, 0, 1),
        ("credentials", "get_credentials", None, 0, 1),
    )
    def __init__(self, url="", options=None, page=None, _realm=None, **_):
        options = options if isinstance(options, dict) else {}
        self._page = page or _realm
        base = self._page.url if self._page else "about:blank"
        self._url = urljoin(base, str(url))
        self._method = str(options.get("method", "GET")).upper()
        self._headers = Headers(options.get("headers"))
        self._body = options.get("body")
        self._mode = str(options.get("mode", "cors"))
        self._credentials = str(options.get("credentials", "same-origin"))
        self._redirect = str(options.get("redirect", "follow"))
        self._timeout = options.get("timeout")
    def get_url(self): return self._url
    def get_method(self): return self._method
    def get_headers(self): return self._headers
    def get_mode(self): return self._mode
    def get_credentials(self): return self._credentials
    def backend_request(self):
        return NetworkRequest(self._url, self._method, dict(self._headers.items()), self._body, self._timeout, self._credentials, self._mode, self._redirect, self._page.url if self._page else None)


@interface("Response", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Response:
    __v8_attribute__ = js_attributes(
        ("status", "get_status", None, 0, 1), ("statusText", "get_statusText", None, 0, 1),
        ("ok", "get_ok", None, 0, 1), ("url", "get_url", None, 0, 1),
        ("redirected", "get_redirected", None, 0, 1), ("type", "get_type", None, 0, 1),
        ("headers", "get_headers", None, 0, 1), ("bodyUsed", "get_bodyUsed", None, 0, 1),
    )
    __v8_method__ = js_methods(("text", "fn_text", 0, 1), ("json", "fn_json", 0, 1), ("arrayBuffer", "fn_arrayBuffer", 0, 1))
    def __init__(self, body="", options=None, page=None, _realm=None, **_):
        options = options if isinstance(options, dict) else {}
        self._page = page or _realm; self.body = body
        self.status = int(options.get("status", 200)); self.status_text = str(options.get("statusText", options.get("reason", "")))
        self.url = str(options.get("url", "")); self.headers = Headers(options.get("headers")); self.redirected = bool(options.get("redirected", False)); self.type = str(options.get("type", "basic")); self.body_used = False
    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_ok(self): return 200 <= self.status < 300
    def get_url(self): return self.url
    def get_redirected(self): return self.redirected
    def get_type(self): return self.type
    def get_headers(self): return self.headers
    def get_bodyUsed(self): return self.body_used
    def _body_value(self):
        self.body_used = True
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else str(self.body)
    def _result(self, value): return self._page.promise_resolve(value) if self._page else value
    def fn_text(self): return self._result(self._body_value())
    def fn_json(self): return self._result(json.loads(self._body_value()))
    def fn_arrayBuffer(self): return self._result(self.body if isinstance(self.body, bytes) else self._body_value().encode())


@interface("MessageEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class MessageEvent(Event):
    __v8_attribute__ = js_attributes(("data", "get_data", None, 0, 1), ("origin", "get_origin", None, 0, 1), ("source", "get_source", None, 0, 1))
    def __init__(self, event_type="message", options=None, **kw):
        super().__init__(event_type, options, **kw); options = options if isinstance(options, dict) else {}; self.data = options.get("data", pyv8.Null); self.origin = options.get("origin", ""); self.source = options.get("source", pyv8.Null)
    def get_data(self): return self.data
    def get_origin(self): return self.origin
    def get_source(self): return self.source


@interface("CloseEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class CloseEvent(Event):
    __v8_attribute__ = js_attributes(("code", "get_code", None, 0, 1), ("reason", "get_reason", None, 0, 1), ("wasClean", "get_wasClean", None, 0, 1))
    def __init__(self, event_type="close", options=None, **kw):
        super().__init__(event_type, options, **kw); options = options if isinstance(options, dict) else {}; self.code = int(options.get("code", 1000)); self.reason = str(options.get("reason", "")); self.was_clean = bool(options.get("wasClean", True))
    def get_code(self): return self.code
    def get_reason(self): return self.reason
    def get_wasClean(self): return self.was_clean


@interface("ErrorEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class ErrorEvent(Event):
    __v8_attribute__ = js_attributes(("message", "get_message", None, 0, 1), ("error", "get_error", None, 0, 1))
    def __init__(self, event_type="error", options=None, **kw):
        super().__init__(event_type, options, **kw); options = options if isinstance(options, dict) else {}; self.message = str(options.get("message", "")); self.error = options.get("error", pyv8.Null)
    def get_message(self): return self.message
    def get_error(self): return self.error


@interface("XMLHttpRequestEventTarget", "EventTarget")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class XMLHttpRequestEventTarget(EventTarget): pass


@interface("XMLHttpRequest", "XMLHttpRequestEventTarget", constructible=True)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class XMLHttpRequest(XMLHttpRequestEventTarget):
    UNSENT, OPENED, HEADERS_RECEIVED, LOADING, DONE = 0, 1, 2, 3, 4
    __v8_attribute__ = js_attributes(
        ("readyState", "get_readyState", None, 0, 1), ("status", "get_status", None, 0, 1),
        ("statusText", "get_statusText", None, 0, 1), ("responseText", "get_responseText", None, 0, 1),
        ("response", "get_response", None, 0, 1),
    )
    __v8_method__ = js_methods(("open", "fn_open", 2, 0), ("send", "fn_send", 0, 0), ("setRequestHeader", "fn_setRequestHeader", 2, 0), ("getResponseHeader", "fn_getResponseHeader", 1, 1), ("abort", "fn_abort", 0, 0))
    def __init__(self, page=None, _realm=None, **kw):
        super().__init__(page=page or _realm, **kw); self._method = "GET"; self._url = ""; self._async = True; self._request_headers = Headers(); self._response = None; self.readyState = self.UNSENT; self.status = 0; self.status_text = ""; self.responseText = ""; self.response = None; self.timeout = 0
    def get_readyState(self): return self.readyState
    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_responseText(self): return self.responseText
    def get_response(self): return self.response
    def _state(self, value): self.readyState = value; self.fn_dispatchEvent(Event("readystatechange"))
    def fn_open(self, method, url, async_value=True, *args):
        self._method, self._url, self._async = str(method).upper(), str(url), bool(async_value); self._state(self.OPENED)
        if not self._async: raise NotSupportedError("synchronous XMLHttpRequest is not supported")
    def fn_setRequestHeader(self, key, value):
        if self.readyState != self.OPENED: raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        self._request_headers.fn_set(key, value)
    def fn_getResponseHeader(self, name):
        if not self._response: return pyv8.Null
        return self._response.headers.fn_get(name)
    def fn_send(self, body=None):
        if self.readyState != self.OPENED: raise InvalidStateError("XMLHttpRequest is not in the OPENED state")
        request = {"method": self._method, "headers": self._request_headers, "body": body, "timeout": self.timeout / 1000 if self.timeout else None}
        if self._page:
            self._page.load_resource_async(self._url, request, self._finish)
    def _finish(self, response, error):
        if not self._page or self._page.closed: return
        if error:
            self.status = 0; self._state(self.DONE); self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error)})); self.fn_dispatchEvent(Event("loadend")); return
        self._response = response; self.status = response.status; self.status_text = response.status_text; self.responseText = response._body_value(); self.response = self.responseText; self._state(self.HEADERS_RECEIVED); self._state(self.LOADING); self._state(self.DONE); self.fn_dispatchEvent(Event("load")); self.fn_dispatchEvent(Event("loadend"))
    def fn_abort(self):
        self.readyState = self.UNSENT; self.fn_dispatchEvent(Event("abort")); self.fn_dispatchEvent(Event("loadend"))


@interface("WebSocket", "EventTarget", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class WebSocket(EventTarget):
    CONNECTING, OPEN, CLOSING, CLOSED = 0, 1, 2, 3
    __v8_attribute__ = js_attributes(("url", "get_url", None, 0, 1), ("protocol", "get_protocol", None, 0, 1), ("readyState", "get_readyState", None, 0, 1), ("extensions", "get_extensions", None, 0, 1), ("binaryType", "get_binaryType", "set_binaryType", 0, 0), ("bufferedAmount", "get_bufferedAmount", None, 0, 1), ("onopen", "get_onopen", "set_onopen", 0, 0), ("onmessage", "get_onmessage", "set_onmessage", 0, 0), ("onerror", "get_onerror", "set_onerror", 0, 0), ("onclose", "get_onclose", "set_onclose", 0, 0))
    __v8_method__ = js_methods(("send", "fn_send", 1, 0), ("close", "fn_close", 2, 0))
    def __init__(self, url="", protocols=None, _realm=None, **kw):
        page = _realm; super().__init__(page=page, **kw)
        if not page or not page.network_backend: raise NetworkUnavailableError("WebSocket requires real_network=True")
        self._page, self._url, self._protocol, self._extensions = page, str(url), "", ""; self._state_value = self.CONNECTING; self._binary_type = "blob"; self._buffered = 0; self._ws = None; self._protocols = protocols
        callbacks = {"open": self._on_open, "message": self._on_message, "error": self._on_error, "close": self._on_close}
        self._ws = page.network_backend.websocket(page, self._url, protocols, callbacks); page._owned_websockets.add(self._ws)
    def get_url(self): return self._url
    def get_protocol(self): return self._protocol
    def get_readyState(self): return self._state_value
    def get_extensions(self): return self._extensions
    def get_binaryType(self): return self._binary_type
    def set_binaryType(self, value):
        if str(value) not in ("blob", "arraybuffer"): raise V8SyntaxError("invalid binaryType")
        self._binary_type = str(value)
    def get_bufferedAmount(self): return self._buffered
    def get_onopen(self): return self._handlers.get("onopen", pyv8.Null)
    def set_onopen(self, value): self._handlers["onopen"] = value
    def get_onmessage(self): return self._handlers.get("onmessage", pyv8.Null)
    def set_onmessage(self, value): self._handlers["onmessage"] = value
    def get_onerror(self): return self._handlers.get("onerror", pyv8.Null)
    def set_onerror(self, value): self._handlers["onerror"] = value
    def get_onclose(self): return self._handlers.get("onclose", pyv8.Null)
    def set_onclose(self, value): self._handlers["onclose"] = value
    def fn_send(self, value):
        if self._state_value != self.OPEN: raise InvalidStateError("WebSocket is not open")
        binary = _bytes_from_value(value)
        if binary is not None and not isinstance(value, str) and hasattr(self._ws, "send_bytes"):
            self._ws.send_bytes(binary)
        else:
            self._ws.send(str(value))
    def fn_close(self, code=1000, reason=""):
        if self._state_value in (self.CLOSING, self.CLOSED): return
        self._state_value = self.CLOSING
        if self._ws: self._ws.close(int(code), str(reason))
    def _on_open(self, ws):
        self._state_value = self.OPEN; self._protocol = getattr(ws, "protocol", "") or ""; self.fn_dispatchEvent(Event("open"))
    def _on_message(self, ws, data, is_text=True):
        if is_text:
            value = str(data)
        elif self._binary_type == "blob":
            value = Blob([_bytes_from_value(data) or b""])
        else:
            value = _bytes_from_value(data) or b""
        self.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self._page.url, "source": self._page.window}))
    def _on_error(self, ws, error, net_error=None): self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error)}))
    def _on_close(self, ws, code, reason, was_clean=True):
        self._state_value = self.CLOSED; self.fn_dispatchEvent(CloseEvent("close", {"code": code, "reason": reason, "wasClean": was_clean})); self._page._owned_websockets.discard(ws)


@interface("WorkerGlobalScope", "EventTarget", constructible=False, exposed_in=("Worker",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class WorkerGlobalScope(EventTarget):
    __v8_global_this__ = True
    __v8_attribute__ = js_attributes(
        ("self", "get_self", None, 0, 1),
        ("onmessage", "get_onmessage", "set_onmessage", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
    )
    __v8_method__ = js_methods(("postMessage", "fn_postMessage", 1, 0), ("close", "fn_close", 0, 0))
    def __init__(self, runtime=None, **kw): super().__init__(page=None, **kw); self._runtime = runtime
    def get_self(self): return self
    def get_onmessage(self): return self._handlers.get("onmessage", pyv8.Null)
    def set_onmessage(self, value): self._handlers["onmessage"] = value
    def get_onerror(self): return self._handlers.get("onerror", pyv8.Null)
    def set_onerror(self, value): self._handlers["onerror"] = value
    def fn_postMessage(self, value): self._runtime.post_to_parent(value)
    def fn_close(self): self._runtime.stop()


@interface("DedicatedWorkerGlobalScope", "WorkerGlobalScope", constructible=False, exposed_in=("Worker",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class DedicatedWorkerGlobalScope(WorkerGlobalScope): pass


class _WorkerRuntime:
    def __init__(self, owner, script_url):
        self.owner = owner; self.page = owner._page; self.script_url = str(script_url); self._queue = queue.Queue(); self._stop = threading.Event(); self._ready = threading.Event(); self._thread = threading.Thread(target=self._run, name="pyv8c-worker", daemon=True); self._isolate = None; self._context = None; self._scope = None; self._thread.start(); self._ready.wait(5)

    def _run(self):
        try:
            source = self.page.resolve_worker_source(self.script_url)
            self._scope = DedicatedWorkerGlobalScope(runtime=self)
            browser = self.page.context_owner.browser
            native_class_configs = browser.native_class_configs
            self._binding_session = browser.interface_universe.binding_session(
                self.page if native_class_configs else None,
                isolate_classes=not native_class_configs,
            )
            native_lazy_bindings = bool(getattr(pyv8.Context, "supports_live_class_configs", False))
            worker_configs = self._binding_session.class_configs(
                "Worker", roots=("DedicatedWorkerGlobalScope",),
            )
            if not native_class_configs:
                worker_configs = {}
                for name in self.page.context_owner.browser.interface_universe.exposed_names("Worker"):
                    self._binding_session.materialize(name)
                worker_configs.update(self._binding_session.configs)
                for cls, config in worker_configs.items():
                    self._binding_session.install_class_config(cls, config)
            if not native_class_configs:
                self._scope.__class__ = self._binding_session.class_for("DedicatedWorkerGlobalScope")
            self._isolate = pyv8.Isolate()
            context_kwargs = {"hook": False, "ctx_type": "Worker"}
            if native_class_configs:
                context_kwargs["class_configs"] = self._binding_session.configs
            self._context = self._isolate.context(self._scope, **context_kwargs)
            from ..bindings.registry import expose_interfaces
            expose_interfaces(
                self._context,
                context_type="Worker",
                generate_stubs=self.page.context_owner.browser.generate_interface_stubs,
                expose_unimplemented=self.page.context_owner.browser.expose_unimplemented,
                stub_logging=self.page.context_owner.browser.stub_logging,
                universe=self.page.context_owner.browser.interface_universe,
                lazy_interfaces=False,
                class_for=self._binding_session.class_for,
            )
            if source:
                self._context.exec_js(source, filename=self.script_url)
            self._ready.set()
            while not self._stop.is_set():
                try: value = self._queue.get(timeout=0.05)
                except queue.Empty: continue
                if value is None: break
                try:
                    self._scope.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self.page.url}))
                except BaseException as exc:
                    self._report_error(exc)
        except BaseException as exc:
            self._report_error(exc)
            self._ready.set()
        finally:
            context = self._context
            self._context = None; self._scope = None
            del context
            if self._isolate is not None:
                try: self._isolate.close()
                except RuntimeError: pass
                self._isolate = None
            self.page.post_task(self.owner._runtime_finished)

    def _report_error(self, exc):
        self.page.post_task(lambda: self.owner._receive_error(exc))

    def post_from_parent(self, value):
        if not self._stop.is_set(): self._queue.put(structured_clone(value))

    def post_to_parent(self, value):
        try:
            value = structured_clone(value, context=self._context)
        except BaseException as exc:
            self._report_error(exc)
            return
        self.page.post_task(lambda: self.owner._receive_message(value))

    def stop(self):
        self._stop.set(); self._queue.put(None)
        if threading.current_thread() is not self._thread: self._thread.join(timeout=5)


@interface("Worker", "EventTarget", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Worker(EventTarget):
    __v8_attribute__ = js_attributes(("onmessage", "get_onmessage", "set_onmessage", 0, 0), ("onerror", "get_onerror", "set_onerror", 0, 0))
    __v8_method__ = js_methods(("postMessage", "fn_postMessage", 1, 0), ("terminate", "fn_terminate", 0, 0))
    def __init__(self, script_url="", _realm=None, **kw):
        page = _realm; super().__init__(page=page, **kw); self._page = page; self._terminated = False; self._lifecycle_finished = False; self._script_url = urljoin(page.url, str(script_url)) if page and not str(script_url).startswith("blob:") else str(script_url)
        if page is not None: page._owned_workers.add(self); page.begin_worker()
        self._runtime = _WorkerRuntime(self, self._script_url)
    def get_onmessage(self): return self._handlers.get("onmessage", pyv8.Null)
    def set_onmessage(self, value): self._handlers["onmessage"] = value
    def get_onerror(self): return self._handlers.get("onerror", pyv8.Null)
    def set_onerror(self, value): self._handlers["onerror"] = value
    def fn_postMessage(self, value):
        if self._terminated:
            raise InvalidStateError("Worker has been terminated")
        self._runtime.post_from_parent(value)
    def _receive_message(self, value):
        if not self._terminated: self.fn_dispatchEvent(MessageEvent("message", {"data": value, "origin": self._page.url}))
    def _receive_error(self, error):
        if not self._terminated:
            self.fn_dispatchEvent(ErrorEvent("error", {"message": str(error), "error": error}))
    def _finish_lifecycle(self):
        if getattr(self, "_lifecycle_finished", False):
            return
        self._lifecycle_finished = True
        self._page.complete_worker()
        self._page._owned_workers.discard(self)
    def _runtime_finished(self):
        self._finish_lifecycle()
    def fn_terminate(self):
        if not self._terminated:
            self._terminated = True
            self._runtime.stop()
            self._finish_lifecycle()
