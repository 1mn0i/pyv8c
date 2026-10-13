"""Child document creation, commit and completion, owned by the parent Page."""
from urllib.parse import urljoin
from ...web.bom.utils import page_base_url


class FrameResourceController:
    def __init__(self, page, controller):
        self.page, self.controller = page, controller

    def discard_iframe_context(self, iframe):
        self.page.document_resources.cancel_node(iframe)
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
        """Create the complete initial about:blank in the inserting turn."""
        if getattr(iframe, "_child_services", None) is not None:
            return iframe._child_services
        page = self.page
        generation = page.navigation.generation
        child = self._child_frame_page(iframe, "about:blank")
        child.navigation.initialize_empty_document()
        markup = iframe.attributes.get("srcdoc")
        if markup is not None:
            # A srcdoc script must not run before appendChild returns. Keep the
            # initial window available while that document is waiting to commit.
            def commit_markup():
                if (not page.closed and page.navigation.generation == generation
                        and iframe._child_services is child.services and iframe.isConnected
                        and iframe.attributes.get("srcdoc") == markup):
                    self.load_iframe_markup(iframe, str(markup))
            page.tasks.post(commit_markup)
            return child
        target = urljoin(page_base_url(page), iframe.get_src() or "about:blank")
        if target != "about:blank":
            return child
        # Chrome fires the initial empty-document load before insertion returns.
        # It is not a navigation: writes from that turn keep this document.
        iframe._resource_started = True
        self.dispatch_frame_completion(iframe, True)
        return child

    def load_iframe_markup(self, iframe, markup):
        """Load markup the frame carries itself; nothing is fetched."""
        page = self.page
        self.page.document_resources.cancel_node(iframe)
        child = self._child_frame_page(iframe, "about:srcdoc")
        child.set_content(markup, url="about:srcdoc")
        generation, child_generation = page.navigation.generation, child.navigation.generation
        def complete(ok):
            if (not page.closed and page.navigation.generation == generation
                    and iframe._child_services is child.services and not child.closed
                    and child.navigation.generation == child_generation):
                self.dispatch_frame_completion(iframe, ok)
        child.lifecycle.when_loaded(lambda ok: page.tasks.post(lambda: complete(ok)))
        return child

    def _child_frame_page(self, iframe, target):
        """Return the live child page for ``iframe``, creating it on demand."""
        from ..page import Page

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
        target = response.url or urljoin(page_base_url(page), iframe.get_src() or "about:blank")
        child = self._child_frame_page(iframe, target)
        try:
            child.set_content(response.body_text(), url=target)
            child.lifecycle.when_loaded(callback)
        except BaseException:  # a failed document load is reported as False
            callback(False)
        return child

    def load_iframe(self, iframe):
        page = self.page
        self.page.document_resources.cancel_node(iframe)
        markup = iframe.attributes.get("srcdoc")
        if markup is not None:
            return self.load_iframe_markup(iframe, str(markup))
        target = urljoin(page_base_url(page), iframe.get_src() or "about:blank")
        if page.navigation.has_navigation and not page.navigation.parsing:
            self.page.document_resources.discover_element_resource(iframe)
            return getattr(iframe, "_child_services", None)
        child = self._child_frame_page(iframe, target)
        response = self.controller.load_resource(
            target, {"method": "GET", "mode": "navigate", "destination": "iframe", "resource_type": "iframe"})
        child.set_content(response.body_text(), url=target)
        return child


