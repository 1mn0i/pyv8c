import itertools
import time
import uuid
import re
from urllib.parse import urljoin, urlsplit

import pyv8

from ..bom.api import Window
from ..dom.model import Document, DocumentType, Event
from ..network.api import Blob, Response, Request, Headers, XMLHttpRequest, Worker, WebSocket, ErrorEvent
from ..network.backend import CronetNetworkBackend, NetworkRequest, NetworkUnavailableError, same_origin
from ..bindings.registry import expose_interfaces
from ..bindings.universe import BaselineRepository, InterfaceUniverse
from ..dom.parser import parse, IncrementalDOMParser
from ..core.errors import InvalidStateError, NetworkError, SecurityError, TimeoutError, V8Error, as_js_error
from .tasks import TaskQueue
from .controllers import (
    DocumentLifecycle, NavigationController, ResourceController,
    ScriptController, StateAlias,
)


class Page:
    _navigation_generation = StateAlias("navigation", "generation")
    _has_navigation = StateAlias("navigation", "has_navigation")
    _pending_navigation = StateAlias("navigation", "pending")
    _parser = StateAlias("navigation", "parser")
    _parser_generation = StateAlias("navigation", "parser_generation")
    _parsing = StateAlias("navigation", "parsing")
    _parser_done = StateAlias("navigation", "parser_done")
    _dom_content_loaded = StateAlias("lifecycle", "dom_content_loaded")
    _document_load_fired = StateAlias("lifecycle", "document_load_fired")
    _load_waiters = StateAlias("lifecycle", "load_waiters")
    _dom_blocking_scripts = StateAlias("scripts", "dom_blocking_scripts")
    _script_queue = StateAlias("scripts", "queue")
    _defer_scripts = StateAlias("scripts", "defer")
    _module_cache = StateAlias("scripts", "module_cache")
    _document_resources = StateAlias("resources", "resources")
    _resource_counter = StateAlias("resources", "counter")

    def __init__(self, context, url="about:blank"):
        self.context_owner = context
        self.closed = False
        self.navigation = NavigationController()
        self.lifecycle = DocumentLifecycle()
        self.scripts = ScriptController()
        self.resources = ResourceController()
        self.tasks = TaskQueue(self, self._run_queued_callback)
        self.microtasks = self.tasks.microtasks
        self._task_errors = self.tasks.errors
        self._promise_counter = itertools.count(1)
        self._owned_websockets = set()
        self._owned_workers = set()
        self._owned_frames = set()
        self.document = Document(page=self, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.network_backend = context.network_backend
        self.window = Window(page=self)
        self.binding_session = context.browser.interface_universe.binding_session(self)
        self.window.location.href = str(url)
        self.context = pyv8.Context(
            self.window, hook=False, ctx_type="Window",
            class_configs=self.binding_session.class_configs("Window", roots=("Window",)),
        )
        expose_interfaces(
            self.context,
            context_type="Window",
            generate_stubs=self.context_owner.browser.generate_interface_stubs,
            expose_unimplemented=self.context_owner.browser.expose_unimplemented,
            stub_logging=self.context_owner.browser.stub_logging,
            universe=self.context_owner.browser.interface_universe,
        )

    @property
    def url(self): return self.window.location.href

    @url.setter
    def url(self, value): self.goto(value)

    def _navigation_allowed(self):
        return not self._has_navigation or self._dispatch_beforeunload()

    def _commit_content(self, html, url):
        if self._has_navigation:
            self._dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self._dispatch_page_event("unload", self.window, {"bubbles": False})
        self._navigation_generation += 1
        self._has_navigation = True
        self._parser_done = self._dom_content_loaded = self._document_load_fired = False
        self._document_resources.clear(); self._dom_blocking_scripts = 0; self._script_queue = []; self._defer_scripts = []; self._module_cache = {}
        self.window.location.href = str(url)
        self.document = Document(page=self, url=str(url), ready_state="loading")
        self._parser_generation = self._navigation_generation
        self._parser = IncrementalDOMParser(self, self.document, str(html))
        self._parsing = True
        # HTML parsing runs on the caller thread until a parser-blocking
        # external script is encountered. Lifecycle notifications remain
        # queued, matching the browser's observable task boundary.
        self._resume_parser(self._parser_generation)
        return self

    def set_content(self, html, url="about:blank"):
        if self.closed or self.context_owner.closed: raise InvalidStateError("Page is closed")
        if not self._navigation_allowed():
            return self
        return self._commit_content(html, url)

    @staticmethod
    def _decode_html(response):
        body = response.body
        if not isinstance(body, bytes):
            return str(body)
        content_type = response.headers.fn_get("content-type")
        match = re.search(r"charset\s*=\s*['\"]?([^\s;'\"]+)", str(content_type), re.I)
        encoding = match.group(1) if match else "utf-8"
        try:
            return body.decode(encoding, "replace")
        except LookupError:
            return body.decode("utf-8", "replace")

    def goto(self, url, *, timeout=30, wait_until="load"):
        """Download and navigate to an HTML document."""
        if self.closed or self.context_owner.closed:
            raise InvalidStateError("Page is closed")
        states = {
            None: None,
            "commit": None,
            "domcontentloaded": lambda: self._dom_content_loaded,
            "load": lambda: self._document_load_fired,
        }
        if wait_until not in states:
            raise ValueError("wait_until must be 'commit', 'domcontentloaded', or 'load'")
        target = urljoin(self.url, str(url))
        if not self._navigation_allowed():
            return None
        timeout_value = None if timeout is None else max(0.0, float(timeout))
        response = self.load_resource(target, {
            "method": "GET", "mode": "navigate", "credentials": "include",
            "timeout": timeout_value,
        })
        final_url = response.url or target
        self._commit_content(self._decode_html(response), final_url)
        ready = states[wait_until]
        if ready is not None:
            deadline = time.monotonic() + (30.0 if timeout_value is None else timeout_value)
            while not ready() and time.monotonic() < deadline:
                self.run_until_idle(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
            if not ready():
                raise TimeoutError(f"Navigation to {target} timed out")
        return response

    def request_navigation(self, url):
        """Queue navigation requested by the JavaScript Location object."""
        if self.closed or self.context_owner.closed:
            raise InvalidStateError("Page is closed")
        target = urljoin(self.url, str(url))
        self._pending_navigation += 1
        request_id = self._pending_navigation

        def navigate():
            if request_id == self._pending_navigation and not self.closed:
                self.goto(target)

        self.post_task(navigate)
        return target

    def _dispatch_page_event(self, event_type, target, options=None):
        event = Event(event_type, options or {})
        event._trusted = True
        if event_type in ("pagehide", "pageshow"): event._persisted = bool((options or {}).get("persisted", False))
        target.fn_dispatchEvent(event)
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
        self._parsing = False
        self.post_task(self._run_deferred_scripts)

    def _parser_is_blocked(self):
        return bool(self._dom_blocking_scripts)

    def _resume_parser(self, generation=None):
        if self.closed or generation != self._navigation_generation or self._parser is None:
            return
        if self._parser_is_blocked():
            return
        self._parsing = True
        try:
            complete = self._parser.run()
        except BaseException as exc:
            self._parsing = False
            self._dispatch_script_error(None, exc)
            complete = True
        if complete and self._parser is not None and self._parser.done:
            self._finish_document_parse()
        else:
            self._parsing = False

    def _parser_script_start(self, node):
        node._parser_script = True
        node._resource_started = False
        node._resource_done = False
        node._resource_error = None
        node._resource_response = None
        # A script fetch starts when its start tag is seen.  The parser may
        # still be collecting its raw text, but async/defer/module resources
        # are allowed to progress concurrently with parsing.
        if getattr(node, "get_src", lambda: "")():
            self._discover_element_resource(node)

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
            if str(getattr(node, "get_type", lambda: "")() or "").lower() == "module":
                self._execute_module(source, filename or self.url, set())
            else:
                self.evaluate(source, filename=filename or self.url)
            return True
        except BaseException as exc:
            self._dispatch_script_error(node, exc)
            return False

    def _execute_module(self, source, filename, trail):
        """Execute static module imports in dependency-first order.

        V8's public extension currently exposes classic script execution only.
        This adapter implements the useful static-import subset while keeping
        all module work on the Page/V8 thread.
        """
        from urllib.parse import urljoin
        key = str(filename)
        if key in self._module_cache: return
        if key in trail: return  # cyclic static imports are already evaluating
        trail = set(trail); trail.add(key)
        imports = re.findall(r"(^|;)\s*import\s+([\s\S]*?)\s+from\s+(['\"])([^'\"]+)\3\s*;?", source)
        side_effects = re.findall(r"(?:^|;)\s*import\s*(['\"])([^'\"]+)\1\s*;?", source)
        for _prefix, clause, _quote, specifier in imports:
            dep_url = urljoin(key, specifier)
            dep = self.load_resource(dep_url, {"method": "GET", "mode": "cors"})
            body = dep.body.decode("utf-8", "replace") if isinstance(dep.body, bytes) else str(dep.body)
            self._execute_module(body, dep.url or dep_url, trail)
        for _quote, specifier in side_effects:
            dep_url = urljoin(key, specifier)
            dep = self.load_resource(dep_url, {"method": "GET", "mode": "cors"})
            body = dep.body.decode("utf-8", "replace") if isinstance(dep.body, bytes) else str(dep.body)
            self._execute_module(body, dep.url or dep_url, trail)
        transformed = re.sub(r"(?:^|;)\s*import\s+([\s\S]*?)\s+from\s+(['\"])([^'\"]+)\2\s*;?", "", source)
        transformed = re.sub(r"(?:^|;)\s*import\s*(['\"])([^'\"]+)\1\s*;?", "", transformed)
        transformed = re.sub(r"\bexport\s+default\s+", "", transformed)
        transformed = re.sub(r"\bexport\s+(?=(?:async\s+)?(?:function|class|const|let|var)\b)", "", transformed)
        transformed = re.sub(r"\bexport\s*\{[^}]*\}\s*;?", "", transformed)
        # Imported names are global lexical bindings after dependency
        # evaluation.  Alias forms need a local declaration in this script.
        for _prefix, clause, _quote, _specifier in imports:
            for item in clause.strip().strip("{}").split(","):
                item = item.strip()
                if not item or item == "*": continue
                if item.startswith("*"):
                    continue
                parts = re.split(r"\s+as\s+", item)
                if len(parts) == 2:
                    transformed = "const %s = %s;\n%s" % (parts[1].strip(), parts[0].strip(), transformed)
        self.evaluate(transformed, filename=key)
        self._module_cache[key] = True

    def _parser_script_complete(self, node, source):
        node._raw_text = source
        script_type = str(getattr(node, "get_type", lambda: "")() or "").lower()
        is_module = script_type == "module"
        is_async = bool(getattr(node, "get_async", lambda: False)())
        is_defer = bool(getattr(node, "get_defer", lambda: False)()) or is_module
        node._resource_async = is_async
        node._script_defer = is_defer
        if node.get_src():
            if is_defer:
                self._defer_scripts.append(node)
            elif not is_async:
                if not getattr(node, "_resource_queued", False):
                    self._dom_blocking_scripts += 1; self._script_queue.append(node); node._resource_queued = True
            self._discover_element_resource(node)
        elif source:
            node._inline_source = source
            if is_defer:
                node._resource_done = True; self._defer_scripts.append(node)
            elif is_async:
                self.post_task(lambda n=node: self._execute_script(n, n._inline_source, self.url))
            else:
                self._execute_script(node, source, self.url)

    def _node_adopted(self, node):
        if getattr(node, "localName", "") == "script":
            self._dynamic_script_updated(node)

    def _dynamic_script_updated(self, node):
        if self.closed or getattr(node, "_resource_started", False) and getattr(node, "_dynamic_started", False):
            return
        # Setting src/text on a detached script does not fetch or execute it.
        # The adoption callback will retry once it is connected to this
        # document.
        parent = getattr(node, "parentNode", None)
        connected = False
        while parent is not None:
            if parent is self.document:
                connected = True
                break
            parent = getattr(parent, "parentNode", None)
        if not connected:
            return
        node._dynamic_started = True
        node._parser_script = False
        node._script_defer = False
        node._resource_started = False
        if node.get_src():
            # Dynamically inserted classic scripts are async by default. An
            # explicit async=false script is kept in insertion order.
            node._resource_async = bool(node.get_async())
            if not node._resource_async:
                self._dom_blocking_scripts += 1
                self._script_queue.append(node)
            self._track_document_resource(node, node.get_src(), {"method": "GET", "mode": "same-origin"}, "script")
        elif getattr(node, "_raw_text", ""):
            self.post_task(lambda n=node: self._execute_script(n, n._raw_text, self.url))

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
            if not async_script and not getattr(node, "_script_defer", False) and not getattr(node, "_parser_script", False):
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
        if not self._dom_blocking_scripts and self._parser is not None and not self._parser.done:
            self.post_task(lambda generation=self._navigation_generation: self._resume_parser(generation))
        if self._parser_done: self._run_deferred_scripts()

    def _run_deferred_scripts(self):
        if self.closed or not self._parser_done:
            return
        while self._defer_scripts:
            node = self._defer_scripts[0]
            if not getattr(node, "_resource_done", False):
                return
            self._defer_scripts.pop(0)
            response, error = node._resource_response, node._resource_error
            if error:
                self._dispatch_script_error(node, error)
            else:
                source = getattr(node, "_inline_source", "")
                if response is not None:
                    source = response.body.decode("utf-8", "replace") if isinstance(response.body, bytes) else str(response.body)
                self._execute_script(node, source, (response.url if response is not None else self.url) or self.url)
                if response is not None: self._dispatch_page_event("load", node, {"bubbles": False})
        self._maybe_fire_dom_content_loaded()

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
            if getattr(node, "_script_defer", False):
                self._document_resources.pop(token, None)
                self._run_deferred_scripts()
                self._maybe_fire_document_load()
                return
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
        if str(request.get("mode", "cors")) == "navigate": return response
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
        self.tasks.begin_network()

    def begin_worker(self):
        self.tasks.begin_worker()

    def complete_worker(self):
        self.tasks.complete_worker()

    def complete_network(self):
        self.tasks.complete_network()

    def post_task(self, callback):
        self.tasks.post(callback)

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
        return self.context.exec_js(str(script), filename=filename)

    def _run_callback(self, callback, *args):
        return callback(*args)

    def _run_queued_callback(self, callback, *args):
        """Keep one faulty task from aborting unrelated browser work."""
        try:
            return self._run_callback(callback, *args)
        except BaseException as exc:
            self._task_errors.append(as_js_error(exc))
            return None

    def schedule(self, callback, delay, repeat=False):
        return self.tasks.schedule(callback, delay, repeat)

    def cancel(self, ident): self.tasks.cancel(ident)

    def run_until_idle(self, timeout=0):
        self.tasks.run_until_idle(timeout)

    def wait_for_network(self, timeout=30):
        self.run_until_idle(timeout=timeout)

    def close(self):
        if self.closed: return
        if self._has_navigation:
            self._dispatch_beforeunload()
            self._dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self._dispatch_page_event("unload", self.window, {"bubbles": False})
        self.closed = True; self.tasks.close(); self._document_resources.clear(); self._load_waiters.clear()
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
        from ..dom.model import HTMLElement, HTML_CLASSES
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
        page = Page(self); self.pages.append(page)
        if str(url) != "about:blank":
            try:
                page.goto(url)
            except BaseException:
                page.close()
                raise
        return page
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
