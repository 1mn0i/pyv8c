"""Resource loading and network responses for a single Page."""

import base64
import hashlib
import itertools
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pyv8
from ..foundation.logging import logger


from ..foundation.errors import InvalidStateError, NetworkError, SecurityError
from ..foundation.urls import DEFAULT_PORTS, absolute_url, same_origin
from ..web.fetch.headers import Headers
from ..web.fetch.response import Response
from ..network.models import NetworkRequest
from ..network.redirects import Deliver, RedirectFailure, next_hop
from .request_headers import browser_headers, fetch_mode


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

    def cancel_node(self, node):
        """Cancel an in-flight request owned by a DOM resource node."""
        for token, (_generation, owner, _kind, handle) in tuple(self.resources.items()):
            if owner is node:
                handle.cancel()
                self.resources.pop(token, None)

    def discard_iframe_context(self, iframe):
        self.cancel_node(iframe)
        services = getattr(iframe, "_child_services", None)
        iframe._child_services = None
        if services is None:
            return
        services.closed = True
        page = getattr(getattr(services, "_page_realm", None), "page", None)
        if page is not None and not page.closed:
            # Removal can happen while the parent realm executes; closing the
            # child synchronously would wait on an executor that script can
            # reach, so the teardown is dispatched after the parent turn.
            page.tasks.post(page.close)

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
        default_port = DEFAULT_PORTS.get(parsed.scheme.lower())
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
        # An opaque-redirect response stands for a redirect the page may not read, so
        # what it "contains" is no answer at all.
        if key is None or response is None or response.type == "opaqueredirect":
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

    def _dispatch(self, call, tail):
        """Run one user callback where the browser asks for it, then hand its
        ``(value, error)`` to *tail*.

        Inline, both run where the caller stands.  Offloaded, *call* runs on the pool
        while *tail* runs on the owner thread through the queue, so a tail that reads
        the page still does.
        """
        page = self.page

        def gather():
            try:
                return call(), None
            except BaseException as exc:  # reported to the request's own callback
                return None, exc

        if not page.context_owner.browser.threaded_loaders:
            value, error = gather()
            tail(value, error)
            return

        tasks = page.services.task_services

        def pool_side():
            value, error = gather()
            # Posted first, so an idle waiter never sees neither work nor a count.
            page.tasks.post(lambda: tail(value, error))
            tasks.complete_loader()

        tasks.begin_loader()
        try:
            page.context_owner.browser.loader_pool.submit(pool_side)
        except BaseException as exc:
            page.tasks.post(lambda: tail(None, exc))
            tasks.complete_loader()

    def _call_off(self, call, timeout=None):
        """Run *call* and wait on this thread for its result.

        The wait is a plain event rather than the realm's queue: this path is reached
        from inside a turn (a parser-blocking script, an iframe, a worker source),
        where nothing else can run the queue for it.
        """
        if not self.page.context_owner.browser.threaded_loaders:
            return call()
        page = self.page
        answer = {}
        arrived = threading.Event()

        def run():
            try:
                value, error = call(), None
            except BaseException as exc:
                value, error = None, exc
            answer["value"], answer["error"] = value, error
            arrived.set()

        try:
            page.context_owner.browser.loader_pool.submit(run)
        except BaseException as exc:
            answer["error"] = exc
            arrived.set()
        if not arrived.wait(timeout):
            raise TimeoutError(f"the page's loader did not answer within {timeout} seconds")
        if answer.get("error") is not None:
            raise answer["error"]
        return answer["value"]

    def _intercept(self, target, request):
        interceptor = self.page.resource_interceptor
        if not callable(interceptor):
            return None
        decision = self._call_off(lambda: interceptor(target, dict(request)), request.get("timeout"))
        if decision is None or decision is True:
            return None
        return self._intercepted_response(decision, target)

    def _intercepted_response(self, decision, target):
        """The response one interceptor decision stands for."""
        if decision is False:
            return Response("", {
                "status": 200, "url": target,
                "headers": {"content-type": "text/plain"},
            }, services=self.page.services)
        response = self._response_from_loader(decision, target)
        response._services = self.page.services
        return response

    @staticmethod
    def _network_request(target, request, page):
        request = ResourceController._request_with_environment_headers(request, page, target)
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
    def _destination(request):
        """The Fetch destination a request dict stands for; ``resource_type`` names
        the loader's bucket, so a caller that knows better passes the destination."""
        explicit = request.get("destination")
        if explicit:
            return str(explicit)
        resource_type = str(request.get("resource_type") or "")
        if resource_type in ("document", "iframe", "script", "style", "image"):
            return resource_type
        if resource_type == "media":
            return "video"
        return "empty"

    @staticmethod
    def _request_with_environment_headers(request, page, target):
        """The request dict with the headers the browser would send for it."""
        request = dict(request or {})
        destination = ResourceController._destination(request)
        # An https URL negotiates h2, where Chrome sends no Connection header.
        http_version = 2 if urlsplit(str(target)).scheme == "https" else 1
        request["headers"] = browser_headers(
            page.environment,
            destination=destination,
            mode=fetch_mode(destination, request.get("mode")),
            method=str(request.get("method", "GET")).upper(),
            url=target,
            current_url=page.url,
            browser_initiated=bool(request.get("browser_initiated")),
            user_activated=bool(request.get("user_activated")),
            content_type=request.get("content_type"),
            http_version=http_version,
            extra=ResourceController.header_dict(request.get("headers")),
        )
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
        target = absolute_url(url, page.url)
        given = dict(request or {"method": "GET"})
        request = self._request_with_environment_headers(given, page, target)
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
            response = self.follow(target, given, self.loader_send(page))
            self._store_response(target, request, response)
            return response
        backend = page.network_backend
        if backend is not None and backend.http_available:
            follows_itself = bool(getattr(backend.http, "FOLLOWS_ITSELF", False))
            response = self.follow(target, given, self.engine_send(page, backend, given),
                                   self.apply_response_policy, engine_follows=follows_itself)
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
        self.check_cors(response, request)
        return response

    def check_cors(self, response, request):
        """The check one cross-origin response has to pass.

        A redirect is checked hop by hop rather than only where it lands: Chrome
        refuses an intermediate 302 from another origin that names no ACAO.
        """
        allowed = response.headers.fn_get("access-control-allow-origin")
        allowed = "" if allowed is None or allowed is pyv8.Null else str(allowed)
        if allowed not in ("*", self.page.window.location.get_origin()):
            raise SecurityError("CORS check failed")
        if request.get("credentials") == "include":
            credentials = response.headers.fn_get("access-control-allow-credentials")
            if allowed == "*" or str(credentials).lower() != "true":
                raise SecurityError("credentialed CORS request was not allowed")
        return response

    def follow(self, target, request, send, finalize=None, engine_follows=False):
        """Follow redirects the way a browser does, and answer once.

        The hops are made here rather than by an engine, because only this layer knows
        the page's origin (the CORS check every cross-origin hop must pass), the cookie
        jar and the request's redirect mode.  *send* performs one hop; *finalize* is the
        page-level policy the last response passes before it is delivered.
        """
        carrier = self.hop_carrier(target, request)
        carrier["started"] = time.perf_counter()
        while True:
            response = send(carrier["url"], self.hop_request(request, carrier))
            finished = self.hop_policy(response, request, carrier, engine_follows, finalize)
            if finished is not None:
                return finished
            try:
                decision = self.decide(response, carrier)
            except RedirectFailure as failure:
                raise NetworkError(str(failure)) from failure
            if isinstance(decision, Deliver):
                return self.delivered(response, carrier, request, finalize, decision)
            self.advance(carrier, decision)

    def follow_async(self, target, request, send, finish, finalize=None, engine_follows=False):
        """Follow redirects off the owner thread, answering once at the end.

        *send* performs one hop and calls back on the owner thread, so the chain never
        blocks it; *finish* hears the chain's single outcome.
        """
        carrier = self.hop_carrier(target, request)
        carrier["started"] = time.perf_counter()

        def landed(response, error):
            if error is not None:
                finish(None, error)
                return
            try:
                finished = self.hop_policy(response, request, carrier, engine_follows, finalize)
                if finished is None:
                    decision = self.decide(response, carrier)
                    if isinstance(decision, Deliver):
                        finished = self.delivered(response, carrier, request, finalize, decision)
                    else:
                        self.advance(carrier, decision)
                        send(carrier["url"], self.hop_request(request, carrier), landed)
                        return
            except BaseException as exc:  # the page hears about it, the chain stops
                finish(None, exc)
                return
            finish(finished, None)

        send(carrier["url"], self.hop_request(request, carrier), landed)

    def hop_policy(self, response, request, carrier, engine_follows, finalize):
        """What is settled before a hop is even asked for: the CORS check, and the
        answer for an engine whose following cannot be turned off."""
        if finalize is not None:
            # Only a path that applies the response policy checks its hops: the
            # synchronous loader path never re-ran it, and does not start now.
            self.check_hop(response, request, carrier)
        if engine_follows and response.redirected:
            self.refuse_followed(carrier)
            return self.delivered(response, carrier, request, finalize, Deliver())
        return None

    def hop_carrier(self, target, request):
        """The state one hop carries: where it goes, how, and with which headers."""
        return {
            "url": target,
            "method": str(request.get("method", "GET")),
            "body": request.get("body"),
            "headers": self.header_dict(request.get("headers")),
            "mode": str(request.get("mode", "cors")),
            "redirect": str(request.get("redirect", "follow")),
            "hops": 0,
            "crossed": False,
            "started": 0.0,
            "redirected_at": 0.0,
        }

    def hop_request(self, request, carrier):
        """The request dict for the hop *carrier* describes, before the headers the
        browser adds for its own URL."""
        return {
            **request,
            "method": carrier["method"],
            "body": carrier["body"],
            "headers": carrier["headers"],
        }

    def decide(self, response, carrier):
        """What the response asks the load path to do next."""
        location = response.headers.fn_get("location")
        if location is pyv8.Null or location is pyv8.Undefined:
            location = ""
        return next_hop(
            status=response.status,
            method=carrier["method"],
            url=carrier["url"],
            headers=carrier["headers"],
            body=carrier["body"],
            location=location,
            hops=carrier["hops"],
            mode=carrier["mode"],
            redirect=carrier["redirect"],
        )

    def advance(self, carrier, decision):
        carrier.update(url=decision.url, method=decision.method, body=decision.body,
                       headers=decision.headers, hops=carrier["hops"] + 1,
                       crossed=carrier["crossed"] or decision.cross_origin,
                       redirected_at=time.perf_counter())

    def check_hop(self, response, request, carrier):
        """The per-hop CORS check, made before the next hop is asked for."""
        if carrier["mode"] == "cors" and not same_origin(self.page.url, response.url):
            self.check_cors(response, request)

    def delivered(self, response, carrier, request, finalize, decision):
        """The response a load path answers with once the chain lands."""
        if decision.opaque:
            return self.opaque_redirect(carrier["url"])
        if finalize is not None:
            response = finalize(response, request)
        if carrier["hops"]:
            response.redirected = True
            response._redirect_hops = carrier["hops"]
            response._redirect_crossed = carrier["crossed"]
            response._redirect_span = (carrier["redirected_at"] - carrier["started"]) * 1000.0
        return response

    def refuse_followed(self, carrier):
        """An engine that follows by itself cannot be told to stop, so the modes that
        forbid following are refused rather than quietly obeyed."""
        if carrier["mode"] == "navigate" or carrier["redirect"] == "follow":
            return
        raise NetworkError(
            "this engine follows redirects by itself and cannot be told not to, so"
            f" redirect: {carrier['redirect']!r} cannot be honoured"
        )

    def opaque_redirect(self, url):
        """What ``redirect: "manual"`` reads as: no status, no headers, no body."""
        return Response("", {"status": 0, "url": url, "type": "opaqueredirect"},
                        services=self.page.services)

    def engine_redirect(self, backend, request):
        """What an engine is told about following: this layer's job, unless the engine
        documents that following cannot be turned off."""
        if getattr(backend.http, "FOLLOWS_ITSELF", False):
            return str(request.get("redirect", "follow"))
        return "manual"

    def engine_send(self, page, backend, request):
        """One hop an HTTP engine makes, told not to follow it itself."""
        redirect = self.engine_redirect(backend, request)

        def send(target, hop_request):
            raw = backend.request_sync(
                self._network_request(target, {**hop_request, "redirect": redirect}, page))
            return self.response_object(raw)
        return send

    def engine_send_async(self, page, backend, request, handle):
        """The same hop, on the engine's pool rather than the caller's thread."""
        redirect = self.engine_redirect(backend, request)

        def send(target, hop_request, done):
            if handle.cancelled:
                return
            hop = self._network_request(target, {**hop_request, "redirect": redirect}, page)
            handle._transport = backend.request_async(
                page.services.task_services, hop,
                lambda raw, error: done(None if error else self.response_object(raw), error),
            )
        return send

    def loader_send(self, page):
        """One hop the page's own loader answers, on this thread."""

        def send(target, hop_request):
            request = self._request_with_environment_headers(hop_request, page, target)
            value = self._call_off(lambda: page.resource_loader(target, request), request.get("timeout"))
            return self._response_from_loader(value, target)
        return send

    def loader_send_async(self, page, handle):
        """The same hop, off the caller's thread as a loader is asked for."""

        def send(target, hop_request, done):
            if handle.cancelled:
                return
            request = self._request_with_environment_headers(hop_request, page, target)

            def after_loader(value, error):
                if error is not None:
                    done(None, error)
                    return
                try:
                    response = self._response_from_loader(value, target)
                    response._services = page.services
                except BaseException as exc:
                    done(None, exc)
                    return
                done(response, None)

            self._dispatch(lambda: page.resource_loader(target, request), after_loader)
        return send

    def load_resource_async(self, url, request, callback):
        page = self.page
        handle = RequestHandle()

        def deliver(response, error):
            if handle.cancelled:
                return
            handle.finish()
            callback(response, error)

        def landed(response, error):
            """Cache what the chain answered, then hand it to the page once."""
            if error is None:
                try:
                    self._store_response(target, given, response)
                except BaseException as exc:  # a cache write must not lose the answer
                    response, error = None, exc
            deliver(response, error)

        def load():
            """Everything behind the interceptor: cache, blob, loader or engine."""
            if handle.cancelled:
                return
            cached = self._cached_response(target, request)
            if cached is not None:
                page.tasks.post(lambda: deliver(cached, None))
                return
            if target.split("#", 1)[0] == "about:blank":
                page.tasks.post(lambda: deliver(Response("", {"url": target}, services=page.services), None))
                return
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
                return
            if page.resource_loader:
                self.follow_async(target, given, self.loader_send_async(page, handle), landed,
                                  finalize=self.apply_response_policy)
                return
            backend = page.network_backend
            if backend is not None and backend.http_available:
                follows_itself = bool(getattr(backend.http, "FOLLOWS_ITSELF", False))
                self.follow_async(target, given, self.engine_send_async(page, backend, given, handle),
                                  landed, finalize=self.apply_response_policy,
                                  engine_follows=follows_itself)
                return
            page.tasks.post(
                lambda: deliver(None, NetworkError("Network access requires an explicit resource_loader or real_network=True"))
            )

        if page.closed or page.context_owner.closed:
            page.tasks.post(lambda: deliver(None, InvalidStateError("Page is closed")))
            return handle
        target = urljoin(page.url, str(url))
        given = dict(request or {})
        request = self._request_with_environment_headers(given, page, target)
        interceptor = page.resource_interceptor
        if not callable(interceptor):
            load()
            return handle

        def after_intercept(decision, error):
            if error is not None:
                page.tasks.post(lambda: deliver(None, error))
                return
            if decision is not None and decision is not True:
                try:
                    response = self._intercepted_response(decision, target)
                except BaseException as exc:
                    response, error = None, exc
                page.tasks.post(lambda: deliver(response, error))
                return
            load()

        self._dispatch(lambda: interceptor(target, dict(request)), after_intercept)
        return handle

    def discover_element_resource(self, node):
        page = self.page
        if page.closed or getattr(node, "_resource_started", False):
            return
        tag = getattr(node, "localName", "")
        if tag == "script":
            tokens = page.services.sandbox_tokens
            if tokens is not None and "allow-scripts" not in tokens:
                return
            src = node.get_src() if hasattr(node, "get_src") else node.attributes.get("src", "")
            if not src:
                return
            node._resource_started = True
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
            if node.attributes.get("srcdoc") is not None:
                return  # the element's own markup is the document
            src = node.attributes.get("src", "") or "about:blank"
        else:
            return
        if not src:
            return
        node._resource_started = True
        if tag in ("audio", "video"):
            # A media load starts with loadstart.  Chrome sends it on a task of its own,
            # after the markup around the element has run, and a page relies on that.
            self.page.tasks.post(lambda element=node: self.page.lifecycle.dispatch_page_event(
                "loadstart", element, {"bubbles": False}))
        resource_type = "style" if tag == "link" else "image" if tag == "img" else "media" if tag in ("audio", "video", "source") else tag
        # ``media`` lumps three elements that the browser reports apart.
        destination = {"link": "style", "img": "image", "audio": "audio",
                       "video": "video", "source": "video"}.get(tag, tag)
        request = {"method": "GET", "resource_type": resource_type, "destination": destination}
        if tag == "iframe":
            request["mode"] = "navigate"
        self.track_document_resource(node, src, request, tag)

    def track_document_resource(self, node, url, request, kind):
        page = self.page
        # Where the entry's timing starts: the request is issued from here.
        node._resource_request_start = page.services.realm_services.performance.clock.now_ms()
        token = next(self.counter)
        generation = page.navigation.generation
        handle = RequestHandle()
        self.resources[token] = (generation, node, kind, handle)

        def done(response, error):
            handle.finish()
            page.services.resource_services._untrack_resource(handle)
            if not handle.cancelled:
                page.tasks.post(lambda: self.complete_document_resource(token, generation, response, error, handle))

        # Register before issuing the request: a fast response runs ``done`` on
        # another thread, and untracking an untracked handle would leave its
        # cancel callback behind until the page closes.
        page.services.resource_services._track_resource(handle)
        handle._transport = self.load_resource_async(url, request, done)
        page.lifecycle.dispatch_progress_event(node, "loadstart")
        return handle

    @staticmethod
    def _response_length(response):
        try:
            return len(getattr(response, "body", None) or b"")
        except TypeError:
            return 0

    def record_resource_timing(self, node, kind, response):
        """Hand the realm one ``resource`` record: the URL, the initiator and the span."""
        length = self._response_length(response)
        clock = self.page.services.realm_services.performance.clock
        start = getattr(node, "_resource_request_start", None) or clock.now_ms()
        self.page.services.realm_services.performance.record(
            "resource",
            name=str(getattr(response, "url", "") or ""),
            initiator=str(kind),
            start=start,
            end=clock.now_ms(),
            transferSize=length,
            encodedBodySize=length,
            decodedBodySize=length,
            contentType=self._response_content_type(response),
        )

    @staticmethod
    def _response_content_type(response):
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "fn_get", None) if headers is not None else None
        if getter is None:
            return ""
        value = getter("content-type")
        return "" if value is None or value is pyv8.Null else str(value)

    def complete_document_resource(self, token, generation, response, error, handle=None):
        page = self.page
        item = self.resources.get(token)
        if item is None or generation != page.navigation.generation or page.closed or (handle is not None and handle.cancelled):
            return
        _generation, node, kind, _handle = item
        if error is None and response is not None:
            self.record_resource_timing(node, kind, response)
        if kind == "iframe" and not error:
            # ``when_loaded`` resolves on the child realm's owner thread, and
            # touching page state must happen on this Page's owner thread.
            def child_loaded(ok):
                page.tasks.post(lambda: self.finish_iframe_resource(token, generation, node, ok))
            self.load_iframe_response(node, response, child_loaded)
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
        if kind in ("audio", "video", "source"):
            # Nothing decodes here, so a media element ends the way Chrome ends bytes
            # it cannot use: progress, suspend, error -- and never a load.
            page.lifecycle.dispatch_page_event("progress", node, {"bubbles": False})
            page.lifecycle.dispatch_page_event("suspend", node, {"bubbles": False})
            page.lifecycle.dispatch_page_event("error", node, {"bubbles": False})
            page.lifecycle.maybe_fire_document_load()
            return
        if kind == "img":
            # ``complete`` turns true the moment a load settles, either way.
            node._image_complete = True
        if error:
            if kind == "script":
                page.lifecycle.dispatch_script_error(node, error)
            else:
                page.lifecycle.dispatch_page_event("error", node, {"bubbles": False})
            page.lifecycle.dispatch_progress_event(node, "loadend")
        else:
            if kind == "script":
                page.scripts.execute_script(node, response.body_text(), response.url or page.url)
            elif kind in ("style", "link"):
                # A ``<link rel=stylesheet>`` is tracked under its own tag so that its
                # timing entry reports ``link``, which is the kind it arrives as here.
                node._stylesheet_text = response.body_text()
                # The cascade caches the rules a document's sheets give, so a sheet
                # that arrives after the first layout has to invalidate that cache.
                document = getattr(node, "ownerDocument", None)
                if document is not None:
                    document.style_version = getattr(document, "style_version", 0) + 1
            # The transfer is complete: one progress event carries the whole body.
            length = self._response_length(response)
            page.lifecycle.dispatch_progress_event(
                node, "progress", loaded=length, total=length, length_computable=True,
            )
            page.lifecycle.dispatch_page_event("load", node, {"bubbles": False})
            page.lifecycle.dispatch_progress_event(node, "loadend")
        page.lifecycle.maybe_fire_document_load()

    def finish_iframe_resource(self, token, generation, node, ok):
        page = self.page
        if generation != page.navigation.generation or page.closed:
            return
        item = self.resources.pop(token, None)
        if item is not None:
            item[3].finish()
        self.dispatch_frame_completion(node, ok)

    def dispatch_frame_completion(self, node, ok):
        """The events a frame's document load fires on the frame element."""
        page = self.page
        page.lifecycle.dispatch_page_event("load" if ok else "error", node, {"bubbles": False})
        if ok:
            # Legacy iframe-only signal, fired after the child document loaded.
            page.lifecycle.dispatch_page_event("DOMFrameContentLoaded", node, {"bubbles": False})
        page.lifecycle.dispatch_progress_event(node, "loadend")
        page.lifecycle.maybe_fire_document_load()

    def _sandbox_tokens(self, iframe):
        """The tokens a frame's ``sandbox`` attribute grants; ``None`` if absent."""
        sandbox = iframe.attributes.get("sandbox")
        return None if sandbox is None else frozenset(str(sandbox).lower().split())

    def _frame_origin(self, iframe):
        """A sandboxed frame without same-origin access has an opaque origin."""
        tokens = self._sandbox_tokens(iframe)
        if tokens is None or "allow-same-origin" in tokens:
            return None
        return "opaque", object()

    def ensure_iframe_context(self, iframe):
        """The context an inserted frame gets, on the task after its insertion.

        A browser builds it while inserting the element; building a realm is not
        reentrant with a running script here, so it happens on the next task.
        """
        if getattr(iframe, "_child_services", None) is not None:
            return iframe._child_services
        markup = iframe.attributes.get("srcdoc")
        if markup is not None:
            return self.load_iframe_markup(iframe, str(markup))
        page = self.page
        child = self._child_frame_page(iframe, "about:blank")
        # With a URL or srcdoc the initial about:blank is replaced by the same
        # insertion navigation and must not emit its own iframe load events.
        if iframe.get_src() or "srcdoc" in iframe.attributes:
            return child
        child.set_content("", url="about:blank")
        child.lifecycle.when_loaded(lambda ok: page.tasks.post(
            lambda: self.dispatch_frame_completion(iframe, ok)))
        return child

    def load_iframe_markup(self, iframe, markup):
        """Load markup the frame carries itself; nothing is fetched."""
        page = self.page
        self.cancel_node(iframe)
        child = self._child_frame_page(iframe, "about:srcdoc")
        child.set_content(markup, url="about:srcdoc")
        child.lifecycle.when_loaded(lambda ok: page.tasks.post(
            lambda: self.dispatch_frame_completion(iframe, ok)))
        return child

    def _child_frame_page(self, iframe, target):
        """Return the live child page for ``iframe``, creating it on demand."""
        from .page import Page

        page = self.page
        child_services = getattr(iframe, "_child_services", None)
        child = child_services._page_realm.page if child_services is not None else None
        if child is None or child.closed:
            child = Page(page.context_owner, url=target, environment=page.environment, parent=page,
                         origin=self._frame_origin(iframe))
            iframe._child_services = child.services
            page.services.context_services.track_frame(child)
            devtools = page.context_owner._devtools
            if devtools is not None:
                child._devtools_target = devtools.register_page(child, kind="iframe", parent=page)
        child.services.sandbox_tokens = self._sandbox_tokens(iframe)
        child.window._window_name = str(iframe.attributes.get("name", ""))
        child.window._publish_name()
        child.window._frame_element = iframe
        return child

    def load_iframe_response(self, iframe, response, callback):
        page = self.page
        target = response.url or urljoin(page.url, iframe.get_src() or "about:blank")
        child = self._child_frame_page(iframe, target)
        try:
            child.set_content(response.body_text(), url=target)
            child.lifecycle.when_loaded(callback)
        except BaseException:  # a failed document load is reported as False
            callback(False)

    def load_iframe(self, iframe):
        page = self.page
        self.cancel_node(iframe)
        markup = iframe.attributes.get("srcdoc")
        if markup is not None:
            return self.load_iframe_markup(iframe, str(markup))
        target = urljoin(page.url, iframe.get_src() or "about:blank")
        if page.navigation.has_navigation and not page.navigation.parsing:
            self.discover_element_resource(iframe)
            return getattr(iframe, "_child_services", None)
        child = self._child_frame_page(iframe, target)
        response = self.load_resource(
            target, {"method": "GET", "destination": "iframe", "resource_type": "iframe"})
        child.set_content(response.body_text(), url=target)
        return child

    def resolve_worker_source(self, script_url):
        response = self.load_resource(script_url, {
            "method": "GET", "mode": "same-origin", "destination": "worker",
            "resource_type": "script",
        })
        return response.body_text()
