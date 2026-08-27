import heapq
import itertools
import queue
import threading
import time
import uuid
from urllib.parse import urlsplit

import pyv8

from .bom import Window
from .dom import Document, DocumentType, Event
from .network import Blob, Response, Request, Headers, XMLHttpRequest, Worker, WebSocket, ErrorEvent, set_current_page, current_page
from .network_backend import CronetNetworkBackend, NetworkRequest, NetworkUnavailableError, same_origin
from .interfaces import expose_interfaces
from .interface_runtime import BaselineRepository, InterfaceUniverse
from .parser import parse
from .error import InvalidStateError, NetworkError, SecurityError, V8Error, as_js_error


class Page:
    def __init__(self, context, url="about:blank"):
        self.context_owner = context
        self.closed = False
        self._queue = []
        self._counter = itertools.count(1)
        self._cancelled = set()
        self.microtasks = []
        self._posted = queue.Queue()
        self._task_errors = []
        self._network_condition = threading.Condition()
        self._network_pending = 0
        self._worker_pending = 0
        self._promise_counter = itertools.count(1)
        self._owned_websockets = set()
        self._owned_workers = set()
        self._owned_frames = set()
        self._navigation_generation = 0
        self._has_navigation = False
        self._parsing = False
        self._parser_done = False
        self._dom_content_loaded = False
        self._document_load_fired = False
        self._document_resources = {}
        self._resource_counter = itertools.count(1)
        self._dom_blocking_scripts = 0
        self._script_queue = []
        self._load_waiters = []
        self.document = Document(page=self, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.network_backend = context.network_backend
        self.window = Window(page=self)
        self.context = pyv8.Context(
            self.window, hook=False, ctx_type="Window",
            class_configs=context.browser.interface_universe.class_configs("Window", roots=("Window",)),
        )
        expose_interfaces(
            self.context,
            context_type="Window",
            generate_stubs=self.context_owner.browser.generate_interface_stubs,
            expose_unimplemented=self.context_owner.browser.expose_unimplemented,
            stub_logging=self.context_owner.browser.stub_logging,
            universe=self.context_owner.browser.interface_universe,
        )
        Worker._current_page = self
        XMLHttpRequest._current_page = self

    @property
    def url(self): return self.window.location.href

    def set_content(self, html, url="about:blank"):
        if self.closed or self.context_owner.closed: raise InvalidStateError("Page is closed")
        if self._has_navigation and not self._dispatch_beforeunload():
            return self
        if self._has_navigation:
            self._dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self._dispatch_page_event("unload", self.window, {"bubbles": False})
        self._navigation_generation += 1
        self._has_navigation = True
        self._parser_done = self._dom_content_loaded = self._document_load_fired = False
        self._document_resources.clear(); self._dom_blocking_scripts = 0; self._script_queue = []
        self.window.location.href = str(url)
        self.document = Document(page=self, url=str(url), ready_state="loading")
        self._parsing = True
        try:
            parse(str(html), self, self.document)
        finally:
            self._parsing = False
        self._finish_document_parse()
        return self

    def _dispatch_page_event(self, event_type, target, options=None):
        event = Event(event_type, options or {})
        event._trusted = True
        if event_type in ("pagehide", "pageshow"): event._persisted = bool((options or {}).get("persisted", False))
        previous = current_page(); set_current_page(self)
        try: target.fn_dispatchEvent(event)
        finally: set_current_page(previous)
        return event

    def _dispatch_beforeunload(self):
        event = self._dispatch_page_event("beforeunload", self.window, {"bubbles": False, "cancelable": True})
        return not event.defaultPrevented and event.get_returnValue()

    def _set_ready_state(self, value):
        if self.document.ready_state == value: return
        self.document.ready_state = value
        self._dispatch_page_event("readystatechange", self.document, {"bubbles": False})

    def _finish_document_parse(self):
        self._parser_done = True
        self.post_task(self._maybe_fire_dom_content_loaded)

    def _maybe_fire_dom_content_loaded(self):
        if self.closed or not self._parser_done or self._dom_content_loaded or self._dom_blocking_scripts:
            return
        self._dom_content_loaded = True
        self._set_ready_state("interactive")
        self._dispatch_page_event("DOMContentLoaded", self.document, {"bubbles": True})
        self._maybe_fire_document_load()

    def _maybe_fire_document_load(self):
        if self.closed or not self._dom_content_loaded or self._document_resources or self._document_load_fired:
            return
        self._document_load_fired = True
        self._set_ready_state("complete")
        self._dispatch_page_event("load", self.window, {"bubbles": False})
        self._dispatch_page_event("pageshow", self.window, {"bubbles": False, "persisted": False})
        waiters, self._load_waiters = self._load_waiters, []
        for callback in waiters: callback(True)

    def _when_loaded(self, callback):
        if self._document_load_fired: self.post_task(lambda: callback(True))
        else: self._load_waiters.append(callback)

    def _dispatch_script_error(self, node, error):
        if node is not None: self._dispatch_page_event("error", node, {"bubbles": False})
        event = ErrorEvent("error", {"message": str(error), "error": error})
        event._trusted = True; self.window.fn_dispatchEvent(event)

    def _execute_script(self, node, source, filename=None):
        try:
            self.evaluate(source, filename=filename or self.url)
            return True
        except BaseException as exc:
            self._dispatch_script_error(node, exc)
            return False

    def _parser_script_complete(self, node, source):
        node._raw_text = source
        if node.get_src():
            self._discover_element_resource(node)
        elif source:
            if self._dom_blocking_scripts:
                node._resource_done = True; node._resource_response = None; node._resource_error = None
                node._inline_source = source; self._dom_blocking_scripts += 1; self._script_queue.append(node)
            else:
                self._execute_script(node, source, self.url)

    def install_inline_handler(self, node, name, source):
        try:
            fn = self.context.exec_js("(function(event){" + str(source) + "})")
            node._handlers[str(name).lower()] = fn
        except BaseException as exc:
            self._dispatch_script_error(node, exc)

    def _discover_element_resource(self, node):
        if self.closed or getattr(node, "_resource_started", False): return
        tag = getattr(node, "localName", "")
        if tag == "script":
            src = node.get_src() if hasattr(node, "get_src") else node.attributes.get("src", "")
            if not src: return
            node._resource_started = True; node._resource_kind = "script"
            async_script = bool(node.get_async()) if hasattr(node, "get_async") else "async" in node.attributes
            node._resource_async = async_script
            if not async_script:
                self._dom_blocking_scripts += 1; self._script_queue.append(node)
            self._track_document_resource(node, src, {"method": "GET", "mode": "same-origin"}, "script")
            return
        if tag == "link" and "stylesheet" not in str(node.attributes.get("rel", "")).lower().split(): return
        if tag == "link": src = node.attributes.get("href", "")
        elif tag in ("img", "audio", "video", "source"): src = node.attributes.get("src", "")
        elif tag == "iframe": src = node.attributes.get("src", "") or "about:blank"
        else: return
        if not src: return
        node._resource_started = True; node._resource_kind = tag
        self._track_document_resource(node, src, {"method": "GET"}, tag)

    def _track_document_resource(self, node, url, request, kind):
        token = next(self._resource_counter); generation = self._navigation_generation
        self._document_resources[token] = (generation, node, kind)
        def done(response, error):
            self.post_task(lambda: self._complete_document_resource(token, generation, response, error))
        self.load_resource_async(url, request, done)

    def _drain_script_queue(self):
        while self._script_queue and getattr(self._script_queue[0], "_resource_done", False):
            node = self._script_queue.pop(0)
            response, error = node._resource_response, node._resource_error
            if error:
                self._dispatch_script_error(node, error)
            elif response is None:
                self._execute_script(node, getattr(node, "_inline_source", ""), self.url)
            else:
                body = response.body.decode("utf-8", "replace") if isinstance(response.body, bytes) else str(response.body)
                self._execute_script(node, body, response.url or self.url)
                self._dispatch_page_event("load", node, {"bubbles": False})
            self._dom_blocking_scripts = max(0, self._dom_blocking_scripts - 1)
        if self._parser_done: self._maybe_fire_dom_content_loaded()

    def _complete_document_resource(self, token, generation, response, error):
        item = self._document_resources.get(token)
        if item is None or generation != self._navigation_generation or self.closed: return
        _generation, node, kind = item
        if kind == "iframe" and not error:
            self._load_iframe_response(node, response, lambda ok: self._finish_iframe_resource(token, generation, node, ok))
            return
        self._document_resources.pop(token, None)
        if kind == "script" and not getattr(node, "_resource_async", False):
            node._resource_done, node._resource_response, node._resource_error = True, response, error
            self._drain_script_queue(); self._maybe_fire_document_load(); return
        if error:
            if kind == "script": self._dispatch_script_error(node, error)
            else: self._dispatch_page_event("error", node, {"bubbles": False})
        else:
            if kind == "script":
                body = response.body.decode("utf-8", "replace") if isinstance(response.body, bytes) else str(response.body)
                self._execute_script(node, body, response.url or self.url)
            self._dispatch_page_event("load", node, {"bubbles": False})
        self._maybe_fire_document_load()

    def _finish_iframe_resource(self, token, generation, node, ok):
        if generation != self._navigation_generation or self.closed: return
        self._document_resources.pop(token, None)
        self._dispatch_page_event("load" if ok else "error", node, {"bubbles": False})
        self._maybe_fire_document_load()

    def _load_iframe_response(self, iframe, response, callback):
        target = iframe.get_src() or "about:blank"
        child = Page(self.context_owner, url=target); iframe._child_page = child
        self._owned_frames.add(child)
        try:
            body = response.body.decode("utf-8", "replace") if isinstance(response.body, bytes) else str(response.body)
            child.set_content(body, url=target)
            child._when_loaded(callback)
        except BaseException:
            callback(False)

    def load_resource(self, url, request=None):
        if self.closed or self.context_owner.closed:
            raise InvalidStateError("Page is closed")
        from urllib.parse import urljoin
        target = urljoin(self.url, str(url))
        request = request or {"method": "GET"}
        if target.startswith("blob:"):
            body, content_type = self.context_owner.resolve_blob_url(target)
            return Response(body, {"url": target, "headers": {"content-type": content_type}})
        if self.resource_loader:
            result = self.resource_loader(target, request)
            if isinstance(result, Response):
                if not result.url: result.url = target
                return result
            if isinstance(result, dict): return Response(result.get("body", ""), {**result, "url": target})
            return Response(str(result), {"url": target})
        if self.network_backend:
            req = NetworkRequest(target, str(request.get("method", "GET")).upper(), self._header_dict(request.get("headers")), request.get("body"), request.get("timeout"), request.get("credentials", "same-origin"), request.get("mode", "cors"), request.get("redirect", "follow"), self.url)
            raw = self.network_backend.request_sync(req)
            return self._apply_response_policy(self._response_object(raw), request)
        if target.startswith("about:blank") or target == self.url:
            return Response("", {"url": target})
        raise NetworkError("Network access requires an explicit resource_loader")

    @staticmethod
    def _header_dict(headers):
        if isinstance(headers, Headers): return dict(headers._data)
        if isinstance(headers, dict): return {str(k): str(v) for k, v in headers.items()}
        return {}

    def _response_object(self, raw):
        if isinstance(raw, Response): return raw
        return Response(raw.body, {"status": raw.status, "statusText": raw.status_text, "url": raw.url, "headers": raw.headers, "redirected": raw.redirected, "type": raw.type}, page=self)

    def _create_blob_url(self, blob):
        if not isinstance(blob, Blob): raise V8Error("parameter is not of type Blob")
        return self.context_owner.create_blob_url(blob, page=self)

    def _revoke_blob_url(self, url): self.context_owner.revoke_blob_url(str(url))

    def resolve_worker_source(self, script_url):
        response = self.load_resource(script_url, {"method": "GET", "mode": "same-origin"})
        body = response.body
        return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)

    def _apply_response_policy(self, response, request):
        if same_origin(self.url, response.url): return response
        mode = str(request.get("mode", "cors"))
        if mode == "no-cors":
            response.status, response.body, response.headers, response.type = 0, "", Headers(), "opaque"
            return response
        allowed = response.headers.fn_get("access-control-allow-origin")
        allowed = "" if allowed is None or allowed is pyv8.Null else str(allowed)
        if allowed not in ("*", self.window.location.get_origin()):
            raise SecurityError("CORS check failed")
        if request.get("credentials") == "include":
            credentials = response.headers.fn_get("access-control-allow-credentials")
            if allowed == "*" or str(credentials).lower() != "true":
                raise SecurityError("credentialed CORS request was not allowed")
        return response

    def load_resource_async(self, url, request, callback):
        if self.closed or self.context_owner.closed:
            self.post_task(lambda: callback(None, InvalidStateError("Page is closed")))
            return
        from urllib.parse import urljoin
        target = urljoin(self.url, str(url))
        req = NetworkRequest(target, str(request.get("method", "GET")).upper(), self._header_dict(request.get("headers")), request.get("body"), request.get("timeout"), request.get("credentials", "same-origin"), request.get("mode", "cors"), request.get("redirect", "follow"), self.url)
        if self.resource_loader:
            try:
                result = self.resource_loader(target, request)
                if isinstance(result, Response): response = result; response._page = self
                elif isinstance(result, dict): response = Response(result.get("body", ""), {**result, "url": target})
                else: response = Response(str(result), {"url": target})
                try:
                    response, error = self._apply_response_policy(response, request), None
                except BaseException as exc:
                    response, error = None, exc
            except BaseException as exc:
                response, error = None, exc
            # Fixture loaders use the same asynchronous boundary as Cronet.
            self.post_task(lambda: callback(response, error))
            return
        if self.network_backend:
            self.network_backend.request_async(self, req, lambda raw, error: self._finish_async_response(raw, error, request, callback))
            return
        self.post_task(lambda: callback(None, NetworkError("Network access requires an explicit resource_loader or real_network=True")))

    def _finish_async_response(self, raw, error, request, callback):
        if error:
            callback(None, error); return
        try: callback(self._apply_response_policy(self._response_object(raw), request), None)
        except BaseException as exc: callback(None, exc)

    def begin_network(self):
        with self._network_condition:
            self._network_pending += 1

    def begin_worker(self):
        with self._network_condition:
            self._worker_pending += 1

    def complete_worker(self):
        with self._network_condition:
            self._worker_pending = max(0, self._worker_pending - 1)
            self._network_condition.notify_all()

    def complete_network(self):
        with self._network_condition:
            self._network_pending = max(0, self._network_pending - 1)
            self._network_condition.notify_all()

    def post_task(self, callback):
        if not self.closed:
            self._posted.put(callback)
            with self._network_condition:
                self._network_condition.notify_all()

    def _make_promise(self):
        record = self.context.exec_js("(() => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; })()")
        return record["promise"], record["resolve"], record["reject"]

    def _js_error_value(self, exc):
        """Materialize a rejected Promise reason as a native JS error object."""
        error = as_js_error(exc)
        name = getattr(error, "_name", None)
        if not name:
            name = {
                "V8TypeError": "TypeError", "V8RangeError": "RangeError",
                "V8ReferenceError": "ReferenceError", "V8SyntaxError": "SyntaxError",
                "V8URIError": "URIError", "V8EvalError": "EvalError",
            }.get(type(error).__name__, "Error")
        message = str(error)
        code = getattr(error, "_code", 0)
        factory = self.context.exec_js("(name, message) => { const C = globalThis[name] || Error; return new C(message); }")
        # DOMException's name is stored on the Python instance, while native
        # Error subclasses use the constructor name.  Keep DOMException
        # construction separate so its name/code remain observable.
        if hasattr(error, "_name"):
            dom_factory = self.context.exec_js("(message, name) => new DOMException(message, name)")
            return dom_factory.call(None, message, name)
        return factory.call(None, name, message, code)

    def promise_resolve(self, value):
        promise = self.context.exec_js("Promise")
        resolve = self.context.exec_js("Promise.resolve")
        return resolve.call(promise, value)

    def load_iframe(self, iframe):
        target = iframe.get_src() or "about:blank"
        if self._has_navigation and not self._parsing:
            self._discover_element_resource(iframe)
            return getattr(iframe, "_child_page", None)
        child = Page(self.context_owner, url=target); iframe._child_page = child
        response = self.load_resource(target, {"method": "GET"})
        body = response.body.decode("utf-8", "replace") if isinstance(response.body, bytes) else str(response.body)
        child.set_content(body, url=target)
        return child

    def evaluate(self, script, filename=None):
        if self.closed or self.context_owner.closed: raise InvalidStateError("Page is closed")
        previous = current_page(); set_current_page(self)
        try:
            return self.context.exec_js(str(script), filename=filename)
        finally:
            set_current_page(previous)

    def _run_callback(self, callback, *args):
        previous = current_page(); set_current_page(self)
        try:
            return callback(*args)
        finally:
            set_current_page(previous)

    def _run_queued_callback(self, callback, *args):
        """Keep one faulty task from aborting unrelated browser work."""
        try:
            return self._run_callback(callback, *args)
        except BaseException as exc:
            self._task_errors.append(as_js_error(exc))
            return None

    def schedule(self, callback, delay, repeat=False):
        ident = next(self._counter)
        try:
            delay = max(0.0, float(delay))
        except (TypeError, ValueError):
            delay = 0.0
        heapq.heappush(
            self._queue,
            (time.monotonic() + delay, ident, callback, delay if repeat else None),
        )
        return ident

    def cancel(self, ident): self._cancelled.add(int(ident))

    def run_until_idle(self, timeout=0):
        deadline = time.monotonic() + float(timeout or 0)
        while self.microtasks or self._queue or not self._posted.empty() or (timeout and (self._network_pending or self._worker_pending)):
            while True:
                try: callback = self._posted.get_nowait()
                except queue.Empty: break
                if not self.closed: self._run_queued_callback(callback)
            while self.microtasks:
                callback = self.microtasks.pop(0)
                self._run_queued_callback(callback)
            if not self._queue:
                if self._posted.empty() and not self.microtasks:
                    if timeout and self._network_pending and time.monotonic() < deadline:
                        with self._network_condition:
                            self._network_condition.wait(min(0.01, max(0, deadline - time.monotonic())))
                        continue
                    if timeout and time.monotonic() < deadline:
                        with self._network_condition:
                            self._network_condition.wait(min(0.01, max(0, deadline - time.monotonic())))
                        if not self._posted.empty(): continue
                    break
                continue
            due, ident, callback, interval = self._queue[0]
            now = time.monotonic()
            if due > now:
                if not timeout or now >= deadline:
                    break
                with self._network_condition:
                    self._network_condition.wait(min(due - now, max(0, deadline - now)))
                continue
            heapq.heappop(self._queue)
            if ident in self._cancelled: continue
            self._run_queued_callback(callback)
            if interval is not None and ident not in self._cancelled:
                heapq.heappush(self._queue, (max(due + interval, time.monotonic()), ident, callback, interval))
            if timeout and time.monotonic() >= deadline: break

    def wait_for_network(self, timeout=30):
        self.run_until_idle(timeout=timeout)

    def close(self):
        if self.closed: return
        if self._has_navigation:
            self._dispatch_beforeunload()
            self._dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self._dispatch_page_event("unload", self.window, {"bubbles": False})
        self.closed = True; self._queue.clear(); self.microtasks.clear(); self._document_resources.clear(); self._load_waiters.clear()
        for frame in list(self._owned_frames): frame.close()
        self._owned_frames.clear()
        for ws in list(self._owned_websockets):
            if self.network_backend: self.network_backend.close_websocket(ws)
        self._owned_websockets.clear()
        for worker in list(self._owned_workers): worker.fn_terminate()
        self._owned_workers.clear()
        self.context_owner._discard_page(self)
        close_context = getattr(self.context, "close", None)
        if callable(close_context):
            close_context()

    def element_class(self, tag):
        from .dom import HTMLElement, HTML_CLASSES
        return HTML_CLASSES.get("HTML" + str(tag).capitalize() + "Element", HTMLElement)

    def document_type(self): return DocumentType(page=self)


class BrowserContext:
    def __init__(self, browser):
        self.browser, self.pages = browser, []
        self.network_backend = CronetNetworkBackend(options=browser.cycronet_options) if browser.real_network else None
        self._blob_urls = {}
        self.closed = False

    def _ensure_open(self):
        if self.closed or self.browser._closed:
            raise InvalidStateError("BrowserContext is closed")
    @staticmethod
    def _serialized_origin(url):
        """Return the browser serialized origin used in a Blob URL."""
        try:
            parts = urlsplit(str(url))
            if parts.scheme not in ("http", "https", "ws", "wss", "ftp") or not parts.hostname:
                return "null"
            host = parts.hostname.lower()
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            port = parts.port
            default_port = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}.get(parts.scheme)
            suffix = (":" + str(port)) if port is not None and port != default_port else ""
            return f"{parts.scheme.lower()}://{host}{suffix}"
        except (TypeError, ValueError):
            return "null"

    def create_blob_url(self, blob, page=None):
        self._ensure_open()
        origin = self._serialized_origin(getattr(page, "url", "about:blank"))
        url = "blob:" + origin + "/" + str(uuid.uuid4())
        self._blob_urls[url] = (bytes(blob._data), blob.get_type())
        return url
    def resolve_blob_url(self, url):
        self._ensure_open()
        if url not in self._blob_urls: raise NetworkError("revoked or unknown Blob URL")
        return self._blob_urls[url]
    def revoke_blob_url(self, url):
        self._ensure_open()
        self._blob_urls.pop(url, None)
    def new_page(self, url="about:blank"):
        self._ensure_open()
        page = Page(self, url=url); self.pages.append(page); return page
    def _discard_page(self, page):
        try:
            self.pages.remove(page)
        except ValueError:
            pass
    def close(self):
        if self.closed:
            return
        self.closed = True
        for page in list(self.pages): page.close()
        self.pages.clear()
        self._blob_urls.clear()
        if self.network_backend:
            self.network_backend.close(); self.network_backend = None


class Browser:
    def __init__(self, *, icudtl_path=None, v8_params=None, resource_loader=None,
                 real_network=False, cycronet_options=None,
                 generate_interface_stubs=True, expose_unimplemented=True,
                 stub_logging="info", interface_baseline_path=None):
        self._closed = False
        # pyv8 initializes its process-global V8 runtime during package import.
        # Keep the arguments for API compatibility; the underlying package owns ICU setup.
        self.contexts = []
        self.resource_loader = resource_loader
        self.real_network = bool(real_network)
        self.cycronet_options = dict(cycronet_options or {})
        self.generate_interface_stubs = bool(generate_interface_stubs)
        self.expose_unimplemented = bool(expose_unimplemented)
        self.stub_logging = str(stub_logging or "info")
        snapshot = BaselineRepository.load(interface_baseline_path)
        self.interface_universe = InterfaceUniverse(
            snapshot, stub_logging=self.stub_logging,
            expose_unimplemented=self.expose_unimplemented,
        )
        self.interface_universe.validate()
        self.interface_baseline_path = snapshot.path
        self.interface_baseline_info = snapshot.info
    def new_context(self):
        if self._closed: raise InvalidStateError("Browser is closed")
        context = BrowserContext(self); self.contexts.append(context); return context
    def close(self):
        if self._closed:
            return
        for context in self.contexts: context.close()
        self.contexts.clear(); self._closed = True
