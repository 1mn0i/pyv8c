"""Shared request preparation, source selection and redirect drivers."""
from dataclasses import dataclass
from functools import cached_property
import time
from urllib.parse import urlsplit
import pyv8
from ...foundation.host_errors import LoadFailure
from ...foundation.urls import absolute_url, origin_tuple, serialize_origin, unloadable_scheme
from ...web.bom.utils import page_base_url
from ...web.fetch.headers import Headers
from ...web.fetch.response import Response
from ...network.models import NetworkRequest
from ...network.redirects import Deliver, RedirectFailure, next_hop
from .request_headers import browser_headers, fetch_mode
from .callbacks import CallbackRunner
from .handles import LoadHandle
from .local import data_url_response, extension_response


@dataclass(slots=True)
class LoadRequest:
    target: str
    options: dict
    prepared: dict
    initiator_url: str
    origin: tuple
    generation: int
    cache_key: object


@dataclass(slots=True)
class HopState:
    url: str
    method: str
    body: object
    headers: dict
    mode: str
    redirect: str
    hops: int = 0
    crossed: bool = False
    started: float = 0.0
    redirected_at: float = 0.0


class ResourceFetchController:
    _NO_CORS_DESTINATIONS = ("script", "style", "image", "audio", "video")

    def __init__(self, page):
        self.page = page
        self.cache = page.context_owner.resource_cache

    @cached_property
    def callbacks(self):
        # PageServices is assembled after the controllers, before the first load.
        return CallbackRunner(self.page)

    def convert_response(self, value, target, *, loader=False):
        response = self._response_from_loader(value, target) if loader else self.response_object(value)
        response._services = self.page.services
        return response

    def _intercepted_response(self, decision, target):
        """The response one interceptor decision stands for."""
        if decision is False:
            return Response("", {
                "status": 200, "url": target,
                "headers": {"content-type": "text/plain"},
            }, services=self.page.services)
        response = self.convert_response(decision, target, loader=True)
        return response

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
    def _request_with_environment_headers(request, page, target, initiator_url=None, origin=None):
        """The request dict with the headers the browser would send for it."""
        request = dict(request or {})
        destination = ResourceFetchController._destination(request)
        # An https URL negotiates h2, where Chrome sends no Connection header.
        http_version = 2 if urlsplit(str(target)).scheme == "https" else 1
        #: A worker's request reports the worker's own (often ``blob:``) URL in
        #: ``Referer``/``Sec-Fetch-Site``/``Origin``, not the owner document's.
        request["headers"] = browser_headers(
            page.environment,
            destination=destination,
            mode=fetch_mode(destination, request.get("mode") or (
                "no-cors" if destination in ResourceFetchController._NO_CORS_DESTINATIONS else "cors")),
            method=str(request.get("method", "GET")).upper(),
            url=target,
            current_url=initiator_url or request.get("initiator_url") or page.url,
            current_origin=None if origin is None else serialize_origin(origin),
            browser_initiated=bool(request.get("browser_initiated")),
            user_activated=bool(request.get("user_activated")),
            content_type=request.get("content_type"),
            http_version=http_version,
            extra=ResourceFetchController.header_dict(request.get("headers")),
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

    def _request_mode(self, request):
        """The mode a request stands for: what it states, else what its destination makes
        it, which is how the request's own headers were built."""
        if self._destination(request) in ("document", "iframe"):
            return "navigate"
        stated = request.get("mode")
        if stated is not None:
            return str(stated)
        if self._destination(request) in ResourceFetchController._NO_CORS_DESTINATIONS:
            return "no-cors"
        return "cors"

    def decide(self, response, carrier):
        """What the response asks the load path to do next."""
        location = response.headers.fn_get("location")
        if location is pyv8.Null or location is pyv8.Undefined:
            location = ""
        return next_hop(
            status=response.status,
            method=carrier.method,
            url=carrier.url,
            headers=carrier.headers,
            body=carrier.body,
            location=location,
            hops=carrier.hops,
            mode=carrier.mode,
            redirect=carrier.redirect,
        )

    def advance(self, carrier, decision):
        carrier.url, carrier.method, carrier.body = decision.url, decision.method, decision.body
        carrier.headers = decision.headers
        carrier.hops += 1
        carrier.crossed = carrier.crossed or decision.cross_origin
        carrier.redirected_at = time.perf_counter()

    def refuse_followed(self, carrier):
        """An engine that follows by itself cannot be told to stop, so the modes that
        forbid following are refused rather than quietly obeyed."""
        if carrier.mode == "navigate" or carrier.redirect == "follow":
            return
        raise LoadFailure(
            "this engine follows redirects by itself and cannot be told not to, so"
            f" redirect: {carrier.redirect!r} cannot be honoured"
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

    def resolve_worker_source(self, script_url):
        response = self.load_resource(script_url, {
            "method": "GET", "mode": "same-origin", "destination": "worker",
            "resource_type": "script",
        })
        if response.status and not 200 <= response.status < 300:
            #: A worker whose script does not arrive is a failed load, the way a browser
            #: reads it; the web layer turns that into the Worker's ``error``.  A script
            #: a page made itself -- ``blob:``, ``data:`` -- carries no status and is
            #: not an answer that went wrong.
            raise LoadFailure(f"the worker script {script_url} answered {response.status}")
        return response.body_text()

    def prepare(self, url, options):
        page = self.page
        if page.closed or page.context_owner.closed:
            raise LoadFailure("Page is closed")
        target = absolute_url(url, page_base_url(page))
        given = dict(options or {})
        initiator = str(given.get("initiator_url") or page.url)
        origin = tuple(page.origin)
        if given.get("initiator_url") and urlsplit(initiator).scheme in ("http", "https", "blob"):
            origin = tuple(page.context_owner._blob_origins.get(initiator, origin_tuple(initiator)))
        prepared = self._request_with_environment_headers(given, page, target, initiator, origin)
        key = self.cache.key(target, prepared, enabled=page.cache_enabled, origin=serialize_origin(origin))
        return LoadRequest(target, given, prepared, initiator, origin, page.navigation.generation, key)

    def active(self, load, handle=None):
        return (not self.page.closed and not self.page.context_owner.closed
                and load.generation == self.page.navigation.generation
                and (handle is None or not handle.done))

    def select_source(self, load):
        """One source precedence for both execution drivers, after interception."""
        cached = self.cache.get(load.cache_key)
        if cached is not None:
            return "response", Response(cached["body"], cached, services=self.page.services)
        target = load.target
        if target.split("#", 1)[0].lower() == "about:blank":
            return "response", Response("", {"url": target}, services=self.page.services)
        if target.startswith("blob:"):
            if self._request_mode(load.options) != "navigate" and not self.same_origin_response(target, load):
                raise LoadFailure("Blob URL belongs to another origin")
            body, content_type = self.page.context_owner.resolve_blob_url(target)
            return "response", Response(body, {"url": target, "headers": {"content-type": content_type}}, services=self.page.services)
        if target[:5].lower() == "data:":
            return "response", data_url_response(target, self.page.services)
        extension = extension_response(self.page, target, load.options)
        if extension is not None:
            return "response", extension
        if self.page.resource_loader:
            return "loader", self.page.resource_loader
        blocked = unloadable_scheme(target)
        if blocked is not None:
            raise LoadFailure(blocked)
        backend = self.page.network_backend
        if backend is not None and backend.http_available:
            return "engine", backend
        raise LoadFailure("Network access requires an explicit resource_loader or real_network=True")

    def store(self, load, response):
        if load.cache_key is None:
            return
        payload = {"body": response.body, "status": response.status,
                   "statusText": response.status_text, "url": response.url or load.target,
                   "headers": dict(response.headers._data), "redirected": response.redirected,
                   "type": response.type}
        self.cache.put(load.cache_key, payload)

    def network_request(self, target, request, load, backend):
        return NetworkRequest(url=target, method=str(request.get("method", "GET")).upper(),
                              headers=self.header_dict(request.get("headers")), body=request.get("body"),
                              timeout=request.get("timeout"), credentials=request.get("credentials", "same-origin"),
                              mode=self._request_mode(load.options), redirect=self.engine_redirect(backend, load.options),
                              origin=serialize_origin(load.origin))

    def hop_carrier(self, load):
        options = load.options
        return HopState(url=load.target, method=str(options.get("method", "GET")).upper(),
                        body=options.get("body"), headers=self.header_dict(options.get("headers")),
                        mode=self._request_mode(options), redirect=str(options.get("redirect", "follow")),
                        started=time.perf_counter())

    def hop_request(self, load, carrier):
        if not carrier.hops:
            return dict(load.prepared)
        options = {**load.options, "method": carrier.method, "body": carrier.body, "headers": carrier.headers}
        return self._request_with_environment_headers(options, self.page, carrier.url, load.initiator_url, load.origin)

    def same_origin_response(self, url, load):
        inherited = self.page.context_owner._blob_origins.get(str(url))
        return load.origin == (tuple(inherited) if inherited is not None else origin_tuple(url))

    def check_cors(self, response, load):
        allowed = response.headers.fn_get("access-control-allow-origin")
        allowed = "" if allowed is None or allowed is pyv8.Null else str(allowed)
        if allowed not in ("*", serialize_origin(load.origin)):
            raise LoadFailure("CORS check failed")
        if load.options.get("credentials") == "include":
            credentials = response.headers.fn_get("access-control-allow-credentials")
            if allowed == "*" or str(credentials).lower() != "true":
                raise LoadFailure("credentialed CORS request was not allowed")

    def check_request_origin(self, target, load):
        if self._request_mode(load.options) == "same-origin" and not self.same_origin_response(target, load):
            raise LoadFailure("same-origin request crossed an origin")

    def apply_response_policy(self, response, load):
        mode = self._request_mode(load.options)
        if mode == "navigate" or self.same_origin_response(response.url, load):
            return response
        self.check_request_origin(response.url, load)
        if load.options.get("mode") == "no-cors":
            response.status, response.body, response.headers, response.type = 0, "", Headers(), "opaque"
        elif mode == "cors":
            self.check_cors(response, load)
        return response

    def step(self, response, load, carrier, engine_follows):
        """Shared transition for sync and async: policy, redirect decision, delivery."""
        if carrier.mode == "cors" and not self.same_origin_response(response.url, load):
            self.check_cors(response, load)
        self.check_request_origin(response.url, load)
        if engine_follows and response.redirected:
            self.refuse_followed(carrier)
            decision = Deliver()
        else:
            try:
                decision = self.decide(response, carrier)
            except RedirectFailure as exc:
                raise LoadFailure(str(exc)) from exc
        if not isinstance(decision, Deliver):
            self.check_request_origin(decision.url, load)
            self.advance(carrier, decision)
            return None
        if decision.opaque:
            return self.opaque_redirect(carrier.url)
        response = self.apply_response_policy(response, load)
        if carrier.hops:
            response.redirected = True
            response._redirect_hops = carrier.hops
            response._redirect_crossed = carrier.crossed
            response._redirect_span = (carrier.redirected_at - carrier.started) * 1000.0
        return response

    def send_sync(self, load, carrier, kind, source):
        self.check_request_origin(carrier.url, load)
        request = self.hop_request(load, carrier)
        if kind == "loader":
            value = self.callbacks.call_sync(lambda: source(carrier.url, request), request.get("timeout"))
            response = self.convert_response(value, carrier.url, loader=True)
            return response
        return self.response_object(source.request_sync(self.network_request(carrier.url, request, load, source)))

    def load_resource(self, url, request=None):
        load = self.prepare(url, request)
        interceptor = self.page.resource_interceptor
        if callable(interceptor):
            decision = self.callbacks.call_sync(lambda: interceptor(load.target, dict(load.prepared)), load.options.get("timeout"))
            if not self.active(load):
                raise LoadFailure("Resource owner changed while intercepting")
            if decision is not None and decision is not True:
                return self._intercepted_response(decision, load.target)
        kind, source = self.select_source(load)
        if kind == "response":
            return source
        carrier = self.hop_carrier(load)
        follows = kind == "engine" and bool(getattr(source.http, "FOLLOWS_ITSELF", False))
        while self.active(load):
            response = self.send_sync(load, carrier, kind, source)
            if not self.active(load):
                break
            delivered = self.step(response, load, carrier, follows)
            if delivered is not None:
                self.store(load, delivered)
                return delivered
        raise LoadFailure("Resource owner changed while loading")

    def load_resource_async(self, url, request, callback):
        return self.page.resource_ownership.issue(self._start_async, url, request, callback)

    def _start_async(self, url, request, callback):
        return _AsyncLoad(self, callback).start(url, request)


class _AsyncLoad:
    """One request owns its stages; each hop owns its reply guard."""
    __slots__ = ("fetcher", "callback", "handle", "tasks", "load", "carrier",
                 "kind", "source", "follows", "stage")

    def __init__(self, fetcher, callback):
        self.fetcher, self.callback = fetcher, callback
        self.handle = LoadHandle()
        self.tasks = fetcher.page.tasks
        self.load = self.carrier = self.source = None
        self.kind = None
        self.follows = False
        self.stage = 0

    def active(self):
        if not self.fetcher.active(self.load, self.handle):
            self.handle.cancel()
            return False
        return True

    def deliver(self, response, error):
        from functools import partial
        self.tasks.post(partial(self.settle, response, error))

    def settle(self, response, error):
        page = self.fetcher.page
        if (page.closed or page.context_owner.closed or
                (self.load is not None and self.load.generation != page.navigation.generation)):
            self.handle.cancel()
        if self.handle.finish():
            self.callback(response, error)

    def fail(self, error):
        from functools import partial
        self.tasks.post(partial(self.deliver, None, error))

    def bind(self, transport, stage):
        # Inline callbacks can advance before the previous dispatch returns.
        if stage == self.stage or self.handle.cancelled:
            self.handle.bind_transport(transport)

    def start(self, url, request):
        from functools import partial
        try:
            self.load = self.fetcher.prepare(url, request)
        except BaseException as error:
            if self.fetcher.page.closed or self.fetcher.page.context_owner.closed:
                self.handle.cancel()
            else:
                self.fail(error)
            return self.handle
        interceptor = self.fetcher.page.resource_interceptor
        if callable(interceptor):
            stage = self.stage
            transport = self.fetcher.callbacks.dispatch(
                partial(interceptor, self.load.target, dict(self.load.prepared)), self.intercepted)
            self.bind(transport, stage)
        else:
            self.begin()
        return self.handle

    def intercepted(self, decision, error):
        if not self.active():
            return
        if error is not None:
            self.deliver(None, error)
        elif decision is None or decision is True:
            self.begin()
        else:
            try:
                response = self.fetcher._intercepted_response(decision, self.load.target)
            except BaseException as error:
                self.deliver(None, error)
            else:
                self.deliver(response, None)

    def immediate(self, response):
        if self.active():
            self.deliver(response, None)

    def begin(self):
        from functools import partial
        if not self.active():
            return
        try:
            self.kind, self.source = self.fetcher.select_source(self.load)
            if self.kind == "response":
                self.tasks.post(partial(self.immediate, self.source))
                return
            self.carrier = self.fetcher.hop_carrier(self.load)
            self.follows = self.kind == "engine" and bool(getattr(self.source.http, "FOLLOWS_ITSELF", False))
            self.send()
        except BaseException as error:
            self.fail(error)

    def send(self):
        from functools import partial
        if not self.active():
            return
        self.stage += 1
        stage = self.stage
        try:
            self.fetcher.check_request_origin(self.carrier.url, self.load)
            prepared = self.fetcher.hop_request(self.load, self.carrier)
            reply = _HopReply(self, self.carrier.url, stage)
            if self.kind == "loader":
                transport = self.fetcher.callbacks.dispatch(
                    partial(self.source, self.carrier.url, prepared), reply.receive)
            else:
                request = self.fetcher.network_request(self.carrier.url, prepared, self.load, self.source)
                transport = self.source.request_async(self.fetcher.page.services.task_services,
                                                      request, reply.enqueue)
            self.bind(transport, stage)
        except BaseException as error:
            self.fail(error)

    def landed(self, response, error):
        if not self.active():
            return
        if error is not None:
            self.deliver(None, error)
            return
        try:
            delivered = self.fetcher.step(response, self.load, self.carrier, self.follows)
            if delivered is None:
                self.send()
                return
            if not self.active():
                return
            self.fetcher.store(self.load, delivered)
        except BaseException as error:
            self.deliver(None, error)
        else:
            self.deliver(delivered, None)


class _HopReply:
    __slots__ = ("owner", "target", "stage", "answered")

    def __init__(self, owner, target, stage):
        self.owner, self.target, self.stage = owner, target, stage
        self.answered = False

    def enqueue(self, value, error):
        from functools import partial
        self.owner.tasks.post(partial(self.receive, value, error))

    def receive(self, value, error):
        owner = self.owner
        if self.answered or self.stage != owner.stage:
            return
        self.answered = True
        if not owner.active():
            return
        try:
            response = None if error else owner.fetcher.convert_response(
                value, self.target, loader=owner.kind == "loader")
        except BaseException as error:
            owner.landed(None, error)
        else:
            owner.landed(response, error)
