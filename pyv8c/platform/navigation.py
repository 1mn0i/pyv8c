"""Navigation and HTML document parsing for a single Page."""

import re
from urllib.parse import urljoin

from ..foundation.errors import InvalidStateError, TimeoutError
from ..foundation.tasks import TaskSource
from ..web.dom.model import Document
from ..web.dom.parser import IncrementalDOMParser


class NavigationController:
    """Own navigation and parser state; runs on the owning Page thread.

    The ``Page`` facade forwards its navigation-facing API here so the Page
    class stays a thin composition root.
    """

    def __init__(self, page):
        self.page = page
        self.generation = 0
        self.has_navigation = False
        self.pending = 0
        self.parser = None
        self.parser_generation = 0
        self.parsing = False
        self.parser_done = False

    # -- navigation ----------------------------------------------------

    def navigation_allowed(self):
        return not self.has_navigation or self.page.lifecycle.dispatch_beforeunload()

    def commit_content(self, html, url):
        lifecycle = self.page.lifecycle
        if self.has_navigation:
            lifecycle.dispatch_page_hide(False)
            lifecycle.dispatch_unload()
        self.generation += 1
        self.has_navigation = True
        self.parser_done = lifecycle.dom_content_loaded = lifecycle.document_load_fired = False
        self.page.resources.reset()
        self.page.scripts.reset()
        self.page._update_origin(url)
        self.page.window.location.href = str(url)
        self.page.window.sync_history_url(str(url))
        devtools = self.page.context_owner._devtools
        if devtools is not None:
            devtools.target_info_changed(self.page)
        self.page.document = Document(services=self.page.services, url=str(url), ready_state="loading")
        self.page.services.document = self.page.document
        target = self.page._devtools_target
        if target is not None:
            target.wait_for_startup()
        self.parser_generation = self.generation
        self.parser = IncrementalDOMParser(self.page.services, self.page.document, str(html))
        self.parsing = True
        # HTML parsing runs on the caller thread until a parser-blocking
        # external script is encountered. Lifecycle notifications remain
        # queued, matching the browser's observable task boundary.
        self.resume_parser(self.parser_generation)
        return self.page

    def set_content(self, html, url="about:blank"):
        if self.page.closed or self.page.context_owner.closed:
            raise InvalidStateError("Page is closed")
        if not self.navigation_allowed():
            return self.page
        return self.commit_content(html, url)

    @staticmethod
    def decode_html(response):
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
        if self.page.closed or self.page.context_owner.closed:
            raise InvalidStateError("Page is closed")
        states = {
            None: None,
            "commit": None,
            "domcontentloaded": lambda: self.page.lifecycle.dom_content_loaded,
            "load": lambda: self.page.lifecycle.document_load_fired,
        }
        if wait_until not in states:
            raise ValueError("wait_until must be 'commit', 'domcontentloaded', or 'load'")
        target = urljoin(self.page.url, str(url))
        if not self.navigation_allowed():
            return None
        timeout_value = None if timeout is None else max(0.0, float(timeout))
        response = self.page.resources.load_resource(
            target,
            {
                "method": "GET", "mode": "navigate", "credentials": "include",
                "resource_type": "document",
                "timeout": timeout_value,
            },
        )
        final_url = response.url or target
        self.commit_content(self.decode_html(response), final_url)
        ready = states[wait_until]
        if ready is not None:
            # Pump on the realm owner thread until the lifecycle state flips.
            # The pump sleeps on the task-queue condition and is woken by the
            # very delivery that advances the document, so this is a wait on
            # the lifecycle state rather than a fixed-interval poll.
            limit = 30.0 if timeout_value is None else timeout_value
            if not self.page.tasks.pump_until(ready, timeout=limit):
                raise TimeoutError(f"Navigation to {target} timed out")
        return response

    def request_navigation(self, url):
        """Queue navigation requested by the JavaScript Location object."""
        if self.page.closed or self.page.context_owner.closed:
            raise InvalidStateError("Page is closed")
        target = urljoin(self.page.url, str(url))
        self.pending += 1
        request_id = self.pending

        def navigate():
            if request_id == self.pending and not self.page.closed:
                self.goto(target)

        self.page.tasks.post(navigate)
        return target

    # -- parser --------------------------------------------------------

    def parser_is_blocked(self):
        return bool(self.page.scripts.dom_blocking_scripts)

    def finish_document_parse(self):
        self.parser_done = True
        self.parsing = False
        self.page.tasks.post(self.page.scripts.run_deferred_scripts, TaskSource.PARSER)

    def resume_parser(self, generation=None):
        if self.page.closed or generation != self.generation or self.parser is None:
            return
        if self.parser_is_blocked():
            return
        self.parsing = True
        try:
            complete = self.parser.run()
        except BaseException as exc:
            self.parsing = False
            self.page.lifecycle.dispatch_script_error(None, exc)
            complete = True
        if complete and self.parser is not None and self.parser.done:
            self.finish_document_parse()
        else:
            self.parsing = False

    def parser_script_start(self, node):
        node._parser_script = True
        node._resource_started = False
        node._resource_done = False
        node._resource_error = None
        node._resource_response = None
        # A script fetch starts when its start tag is seen.  The parser may
        # still be collecting its raw text, but async/defer/module resources
        # are allowed to progress concurrently with parsing.
        if getattr(node, "get_src", lambda: "")():
            self.page.resources.discover_element_resource(node)

    def parser_script_complete(self, node, source):
        scripts = self.page.scripts
        node._raw_text = source
        script_type = str(getattr(node, "get_type", lambda: "")() or "").lower()
        is_module = script_type == "module"
        is_async = bool(getattr(node, "get_async", lambda: False)())
        is_defer = bool(getattr(node, "get_defer", lambda: False)()) or is_module
        node._resource_async = is_async
        node._script_defer = is_defer
        if node.get_src():
            if is_defer:
                scripts.defer.append(node)
            elif not is_async:
                if not getattr(node, "_resource_queued", False):
                    scripts.dom_blocking_scripts += 1
                    scripts.queue.append(node)
                    node._resource_queued = True
            self.page.resources.discover_element_resource(node)
        elif source:
            node._inline_source = source
            if is_defer:
                node._resource_done = True
                scripts.defer.append(node)
            elif is_async:
                self.page.tasks.post(lambda n=node: scripts.execute_script(n, n._inline_source, self.page.url))
            else:
                scripts.execute_script(node, source, self.page.url)

    def element_class(self, tag):
        from ..web.dom.model import HTML_CLASSES, HTMLElement
        return HTML_CLASSES.get("HTML" + str(tag).capitalize() + "Element", HTMLElement)
