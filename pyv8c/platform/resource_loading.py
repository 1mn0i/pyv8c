"""Resource loading and network responses for a single Page."""

import itertools
import threading
import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pyv8
from loguru import logger

from ..foundation.errors import InvalidStateError, NetworkError, SecurityError
from ..web.fetch.headers import Headers
from ..web.fetch.response import Response
from ..web.network.backend import NetworkRequest, same_origin


class RequestHandle:
    """Cancellation state for one page-owned asynchronous resource."""

    __slots__ = ("_cancelled", "_done", "_transport", "_scope_callback")

    def __init__(self):
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._transport = None
        self._scope_callback = None

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    @property
    def done(self):
        return self._done.is_set()

    def cancel(self):
        self._cancelled.set()
        transport = self._transport
        if transport is not None:
            transport.cancel()

    def finish(self):
        self._done.set()


class ResourceController:
    """Own in-flight document resources; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.resources = {}
        self.counter = itertools.count(1)

    def reset(self):
        for _generation, _node, _kind, handle in self.resources.values():
            handle.cancel()
        self.resources.clear()

    def _cache_key(self, target, request):
        if not self.page.cache_enabled:
            return None
        method = str(request.get("method", "GET")).upper()
        if method not in ("GET", "HEAD") or str(request.get("cache", "default")) in ("no-store", "reload", "no-cache"):
            return None
        return (
            method,
            target,
            str(request.get("mode", "cors")),
            str(request.get("credentials", "same-origin")),
        )

    def _cached_response(self, target, request):
        key = self._cache_key(target, request)
        if key is None:
            return None
        context = self.page.context_owner
        with context.resource_cache_lock:
            cached = self._read_disk_cache(context, key)
        if cached is None:
            return None
        logger.debug("resource cache hit: {}", target)
        return Response(
            cached["body"],
            {name: value for name, value in cached.items() if name != "body"},
            services=self.page.services,
        )

    @staticmethod
    def _disk_cache_path(context, key):
        cache_dir = context.resource_cache_dir
        if cache_dir is None:
            return None
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        target = str(key[1]) if len(key) > 1 else ""
        parsed = urlsplit(target)
        domain = parsed.hostname or "unknown"
        try:
            port = parsed.port
        except ValueError:
            port = None
        default_port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
        if port is not None and port != default_port:
            domain = f"{domain}_{port}"
        domain = re.sub(r"[^A-Za-z0-9._-]+", "_", domain).strip("._") or "unknown"
        return Path(cache_dir) / domain / (digest + ".json")

    def _read_disk_cache(self, context, key):
        path = self._disk_cache_path(context, key)
        if path is None or not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            body = base64.b64decode(record["body"])
            if record.get("body_encoding") != "bytes":
                body = body.decode("utf-8")
            payload = {
                "body": body,
                "status": int(record.get("status", 200)),
                "statusText": str(record.get("statusText", "")),
                "url": str(record.get("url", "")),
                "headers": dict(record.get("headers", {})),
                "redirected": bool(record.get("redirected", False)),
                "type": str(record.get("type", "basic")),
            }
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return payload

    def _store_response(self, target, request, response):
        key = self._cache_key(target, request)
        if key is None or response is None:
            return
        context = self.page.context_owner
        payload = {
            "body": response.body,
            "status": response.status,
            "statusText": response.status_text,
            "url": response.url or target,
            "headers": dict(response.headers._data),
            "redirected": response.redirected,
            "type": response.type,
        }
        with context.resource_cache_lock:
            path = self._disk_cache_path(context, key)
            if path is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    body = payload["body"]
                    body_encoding = "bytes" if isinstance(body, (bytes, bytearray, memoryview)) else "text"
                    body_bytes = bytes(body) if body_encoding == "bytes" else str(body).encode("utf-8")
                    record = {name: value for name, value in payload.items() if name != "body"}
                    record.update({
                        "body": base64.b64encode(body_bytes).decode("ascii"),
                        "body_encoding": body_encoding,
                    })
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                        json.dump(record, stream, ensure_ascii=True, separators=(",", ":"))
                        temporary = stream.name
                    os.replace(temporary, path)
                except (OSError, TypeError, ValueError):
                    try:
                        if "temporary" in locals():
                            os.unlink(temporary)
                    except OSError:
                        pass

    def _intercept(self, target, request):
        interceptor = self.page.resource_interceptor
        if not callable(interceptor):
            return None
        decision = interceptor(target, dict(request))
        if decision is None or decision is True:
            return None
        if decision is False:
            return Response("", {
                "status": 200, "url": target,
                "headers": {"content-type": "text/plain"},
            }, services=self.page.services)
        response = self._response_from_loader(decision, target)
        response._services = self.page.services
        return response

    # -- synchronous loading -------------------------------------------

    @staticmethod
    def _network_request(target, request, page):
        request = ResourceController._request_with_environment_headers(request, page)
        return NetworkRequest(
            target,
            str(request.get("method", "GET")).upper(),
            ResourceController.header_dict(request.get("headers")),
            request.get("body"),
            request.get("timeout"),
            request.get("credentials", "same-origin"),
            request.get("mode", "cors"),
            request.get("redirect", "follow"),
            page.url,
        )

    @staticmethod
    def _request_with_environment_headers(request, page):
        request = dict(request or {})
        headers = ResourceController.header_dict(request.get("headers"))
        names = {str(key).lower() for key in headers}
        environment = page.environment
        if "user-agent" not in names:
            headers["User-Agent"] = environment.user_agent
        if "accept-language" not in names:
            headers["Accept-Language"] = ", ".join(environment.languages)
        request["headers"] = headers
        return request

    @staticmethod
    def _response_from_loader(result, target):
        """Wrap a resource_loader result into a Response."""
        if isinstance(result, Response):
            if not result.url:
                result.url = target
            return result
        if isinstance(result, dict):
            return Response(result.get("body", ""), {**result, "url": target})
        return Response(str(result), {"url": target})

    def load_resource(self, url, request=None):
        page = self.page
        if page.closed or page.context_owner.closed:
            raise InvalidStateError("Page is closed")
        target = urljoin(page.url, str(url))
        request = self._request_with_environment_headers(request or {"method": "GET"}, page)
        intercepted = self._intercept(target, request)
        if intercepted is not None:
            return intercepted
        cached = self._cached_response(target, request)
        if cached is not None:
            return cached
        if target.startswith("blob:"):
            body, content_type = page.context_owner.resolve_blob_url(target)
            return Response(body, {"url": target, "headers": {"content-type": content_type}})
        if page.resource_loader:
            response = self._response_from_loader(page.resource_loader(target, request), target)
            self._store_response(target, request, response)
            return response
        if page.network_backend:
            req = self._network_request(target, request, page)
            raw = page.network_backend.request_sync(req)
            response = self.apply_response_policy(self.response_object(raw), request)
            self._store_response(target, request, response)
            return response
        if target.startswith("about:blank") or target == page.url:
            return Response("", {"url": target})
        raise NetworkError("Network access requires an explicit resource_loader")

    @staticmethod
    def header_dict(headers):
        if isinstance(headers, Headers):
            return dict(headers._data)
        if isinstance(headers, dict):
            return {str(k): str(v) for k, v in headers.items()}
        return {}

    def response_object(self, raw):
        if isinstance(raw, Response):
            return raw
        return Response(
            raw.body,
            {
                "status": raw.status, "statusText": raw.status_text, "url": raw.url,
                "headers": raw.headers, "redirected": raw.redirected, "type": raw.type,
            },
            services=self.page.services,
        )

    def apply_response_policy(self, response, request):
        if str(request.get("mode", "cors")) == "navigate":
            return response
        if same_origin(self.page.url, response.url):
            return response
        mode = str(request.get("mode", "cors"))
        if mode == "no-cors":
            response.status, response.body, response.headers, response.type = 0, "", Headers(), "opaque"
            return response
        allowed = response.headers.fn_get("access-control-allow-origin")
        allowed = "" if allowed is None or allowed is pyv8.Null else str(allowed)
        if allowed not in ("*", self.page.window.location.get_origin()):
            raise SecurityError("CORS check failed")
        if request.get("credentials") == "include":
            credentials = response.headers.fn_get("access-control-allow-credentials")
            if allowed == "*" or str(credentials).lower() != "true":
                raise SecurityError("credentialed CORS request was not allowed")
        return response

    def load_resource_async(self, url, request, callback):
        page = self.page
        handle = RequestHandle()

        def deliver(response, error):
            if handle.cancelled:
                return
            handle.finish()
            callback(response, error)

        if page.closed or page.context_owner.closed:
            page.tasks.post(lambda: deliver(None, InvalidStateError("Page is closed")))
            return handle
        target = urljoin(page.url, str(url))
        request = self._request_with_environment_headers(request, page)
        try:
            intercepted = self._intercept(target, request)
        except BaseException as exc:
            page.tasks.post(lambda exc=exc: deliver(None, exc))
            return handle
        if intercepted is not None:
            page.tasks.post(lambda: deliver(intercepted, None))
            return handle
        cached = self._cached_response(target, request)
        if cached is not None:
            page.tasks.post(lambda: deliver(cached, None))
            return handle
        if target.startswith("blob:"):
            try:
                body, content_type = page.context_owner.resolve_blob_url(target)
                response, error = Response(
                    body,
                    {"url": target, "headers": {"content-type": content_type}},
                    services=page.services,
                ), None
            except BaseException as exc:
                response, error = None, exc
            page.tasks.post(lambda: deliver(response, error))
            return handle
        req = self._network_request(target, request, page)
        if page.resource_loader:
            try:
                response = self._response_from_loader(page.resource_loader(target, request), target)
                response._services = page.services
                try:
                    response, error = self.apply_response_policy(response, request), None
                    self._store_response(target, request, response)
                except BaseException as exc:
                    response, error = None, exc
            except BaseException as exc:
                response, error = None, exc
            # Fixture loaders use the same asynchronous boundary as Cronet.
            page.tasks.post(lambda: deliver(response, error))
            return handle
        if page.network_backend:
            handle._transport = page.network_backend.request_async(
                page.services, req,
                lambda raw, error: self.finish_async_response(raw, error, request, deliver),
            )
            return handle
        page.tasks.post(
            lambda: deliver(None, NetworkError("Network access requires an explicit resource_loader or real_network=True"))
        )
        return handle

    def finish_async_response(self, raw, error, request, callback):
        if error:
            callback(None, error)
            return
        try:
            response = self.apply_response_policy(self.response_object(raw), request)
            self._store_response(response.url, request, response)
            callback(response, None)
        except BaseException as exc:
            callback(None, exc)

    # -- document-subresource tracking ---------------------------------

    def discover_element_resource(self, node):
        page = self.page
        if page.closed or getattr(node, "_resource_started", False):
            return
        tag = getattr(node, "localName", "")
        if tag == "script":
            src = node.get_src() if hasattr(node, "get_src") else node.attributes.get("src", "")
            if not src:
                return
            node._resource_started = True
            node._resource_kind = "script"
            async_script = bool(node.get_async()) if hasattr(node, "get_async") else "async" in node.attributes
            node._resource_async = async_script
            if not async_script and not getattr(node, "_script_defer", False) and not getattr(node, "_parser_script", False):
                page.scripts.dom_blocking_scripts += 1
                page.scripts.queue.append(node)
            self.track_document_resource(node, src, {"method": "GET", "mode": "same-origin", "resource_type": "script"}, "script")
            return
        if tag == "link" and "stylesheet" not in str(node.attributes.get("rel", "")).lower().split():
            return
        if tag == "link":
            src = node.attributes.get("href", "")
        elif tag in ("img", "audio", "video", "source"):
            src = node.attributes.get("src", "")
        elif tag == "iframe":
            src = node.attributes.get("src", "") or "about:blank"
        else:
            return
        if not src:
            return
        node._resource_started = True
        node._resource_kind = tag
        resource_type = "style" if tag == "link" else "image" if tag == "img" else "media" if tag in ("audio", "video", "source") else tag
        self.track_document_resource(node, src, {"method": "GET", "resource_type": resource_type}, tag)

    def track_document_resource(self, node, url, request, kind):
        page = self.page
        token = next(self.counter)
        generation = page.navigation.generation
        handle = RequestHandle()
        self.resources[token] = (generation, node, kind, handle)

        def done(response, error):
            handle.finish()
            page.services._untrack_resource(handle)
            if not handle.cancelled:
                page.tasks.post(lambda: self.complete_document_resource(token, generation, response, error, handle))

        # Register before issuing the request: a fast response runs ``done`` on
        # another thread, and untracking a handle that was never tracked would
        # silently leave its cancel callback behind until the page closes.
        page.services._track_resource(handle)
        handle._transport = self.load_resource_async(url, request, done)
        return handle

    def complete_document_resource(self, token, generation, response, error, handle=None):
        page = self.page
        item = self.resources.get(token)
        if item is None or generation != page.navigation.generation or page.closed or (handle is not None and handle.cancelled):
            return
        _generation, node, kind, _handle = item
        if kind == "iframe" and not error:
            self.load_iframe_response(node, response, lambda ok: self.finish_iframe_resource(token, generation, node, ok))
            return
        self.resources.pop(token, None)
        _handle.finish()
        if kind == "script" and not getattr(node, "_resource_async", False):
            node._resource_done, node._resource_response, node._resource_error = True, response, error
            if getattr(node, "_script_defer", False):
                self.resources.pop(token, None)
                page.scripts.run_deferred_scripts()
                page.lifecycle.maybe_fire_document_load()
                return
            page.scripts.drain_script_queue()
            page.lifecycle.maybe_fire_document_load()
            return
        if error:
            if kind == "script":
                page.lifecycle.dispatch_script_error(node, error)
            else:
                page.lifecycle.dispatch_page_event("error", node, {"bubbles": False})
        else:
            if kind == "script":
                page.scripts.execute_script(node, response.body_text(), response.url or page.url)
            page.lifecycle.dispatch_page_event("load", node, {"bubbles": False})
        page.lifecycle.maybe_fire_document_load()

    def finish_iframe_resource(self, token, generation, node, ok):
        page = self.page
        if generation != page.navigation.generation or page.closed:
            return
        item = self.resources.pop(token, None)
        if item is not None:
            item[3].finish()
        page.lifecycle.dispatch_page_event("load" if ok else "error", node, {"bubbles": False})
        page.lifecycle.maybe_fire_document_load()

    def load_iframe_response(self, iframe, response, callback):
        from .browser import Page

        page = self.page
        target = iframe.get_src() or "about:blank"
        child = Page(page.context_owner, url=target, environment=page.environment)
        iframe._child_services = child.services
        page.services._track_frame(child)
        devtools = page.context_owner._devtools
        if devtools is not None:
            child._devtools_target = devtools.register_page(child, kind="iframe", parent=page)
        try:
            child.set_content(response.body_text(), url=target)
            child.lifecycle.when_loaded(callback)
        except BaseException:
            callback(False)

    def load_iframe(self, iframe):
        page = self.page
        target = iframe.get_src() or "about:blank"
        if page.navigation.has_navigation and not page.navigation.parsing:
            self.discover_element_resource(iframe)
            return getattr(iframe, "_child_services", None)
        from .browser import Page

        child = Page(page.context_owner, url=target, environment=page.environment)
        iframe._child_services = child.services
        page.services._track_frame(child)
        devtools = page.context_owner._devtools
        if devtools is not None:
            child._devtools_target = devtools.register_page(child, kind="iframe", parent=page)
        response = self.load_resource(target, {"method": "GET"})
        child.set_content(response.body_text(), url=target)
        return child

    def resolve_worker_source(self, script_url):
        response = self.load_resource(script_url, {"method": "GET", "mode": "same-origin"})
        return response.body_text()
