"""Resource loading and network responses for a single Page."""

import itertools
from urllib.parse import urljoin

import pyv8

from ..core.errors import InvalidStateError, NetworkError, SecurityError
from ..network.api import Headers, Response
from ..network.backend import NetworkRequest, same_origin


class ResourceController:
    """Own in-flight document resources; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.resources = {}
        self.counter = itertools.count(1)

    def reset(self):
        self.resources.clear()

    # -- synchronous loading -------------------------------------------

    @staticmethod
    def _network_request(target, request, page):
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
        request = request or {"method": "GET"}
        if target.startswith("blob:"):
            body, content_type = page.context_owner.resolve_blob_url(target)
            return Response(body, {"url": target, "headers": {"content-type": content_type}})
        if page.resource_loader:
            return self._response_from_loader(page.resource_loader(target, request), target)
        if page.network_backend:
            req = self._network_request(target, request, page)
            raw = page.network_backend.request_sync(req)
            return self.apply_response_policy(self.response_object(raw), request)
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
            page=self.page,
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
        if page.closed or page.context_owner.closed:
            page.post_task(lambda: callback(None, InvalidStateError("Page is closed")))
            return
        target = urljoin(page.url, str(url))
        req = self._network_request(target, request, page)
        if page.resource_loader:
            try:
                response = self._response_from_loader(page.resource_loader(target, request), target)
                response._page = page
                try:
                    response, error = self.apply_response_policy(response, request), None
                except BaseException as exc:
                    response, error = None, exc
            except BaseException as exc:
                response, error = None, exc
            # Fixture loaders use the same asynchronous boundary as Cronet.
            page.post_task(lambda: callback(response, error))
            return
        if page.network_backend:
            page.network_backend.request_async(
                page, req, lambda raw, error: self.finish_async_response(raw, error, request, callback)
            )
            return
        page.post_task(
            lambda: callback(None, NetworkError("Network access requires an explicit resource_loader or real_network=True"))
        )

    def finish_async_response(self, raw, error, request, callback):
        if error:
            callback(None, error)
            return
        try:
            callback(self.apply_response_policy(self.response_object(raw), request), None)
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
            self.track_document_resource(node, src, {"method": "GET", "mode": "same-origin"}, "script")
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
        self.track_document_resource(node, src, {"method": "GET"}, tag)

    def track_document_resource(self, node, url, request, kind):
        page = self.page
        token = next(self.counter)
        generation = page.navigation.generation
        self.resources[token] = (generation, node, kind)

        def done(response, error):
            page.post_task(lambda: self.complete_document_resource(token, generation, response, error))

        self.load_resource_async(url, request, done)

    def complete_document_resource(self, token, generation, response, error):
        page = self.page
        item = self.resources.get(token)
        if item is None or generation != page.navigation.generation or page.closed:
            return
        _generation, node, kind = item
        if kind == "iframe" and not error:
            self.load_iframe_response(node, response, lambda ok: self.finish_iframe_resource(token, generation, node, ok))
            return
        self.resources.pop(token, None)
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
        self.resources.pop(token, None)
        page.lifecycle.dispatch_page_event("load" if ok else "error", node, {"bubbles": False})
        page.lifecycle.maybe_fire_document_load()

    def load_iframe_response(self, iframe, response, callback):
        from .browser import Page

        page = self.page
        target = iframe.get_src() or "about:blank"
        child = Page(page.context_owner, url=target)
        iframe._child_page = child
        page._track_frame(child)
        try:
            child.set_content(response.body_text(), url=target)
            child.lifecycle.when_loaded(callback)
        except BaseException:
            callback(False)

    def load_iframe(self, iframe):
        from .browser import Page

        page = self.page
        target = iframe.get_src() or "about:blank"
        if page.navigation.has_navigation and not page.navigation.parsing:
            self.discover_element_resource(iframe)
            return getattr(iframe, "_child_page", None)
        child = Page(page.context_owner, url=target)
        iframe._child_page = child
        response = self.load_resource(target, {"method": "GET"})
        child.set_content(response.body_text(), url=target)
        return child

    def resolve_worker_source(self, script_url):
        response = self.load_resource(script_url, {"method": "GET", "mode": "same-origin"})
        return response.body_text()
