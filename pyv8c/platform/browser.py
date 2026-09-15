"""Browser, browser context and Page orchestration facade.

The heavy lifting lives in small controllers owned by ``Page``:
``platform/navigation.py``, ``platform/lifecycle.py``, ``platform/scripting.py``
and ``platform/resource_loading.py``.  This module stays a thin composition
root: ``Page`` forwards its public API and delegates behaviour to those
controllers, so each concern can be read and tested on its own.
"""

import os
import threading
import time
import uuid
from pathlib import Path
from types import MappingProxyType

# Kept as a module attribute: tests patch ``pyv8c.platform.browser.pyv8``
# to instrument native Context construction on the PageRealm path.
import pyv8  # noqa: F401 - patch target for native Context instrumentation tests
from loguru import logger

from ..engine.universe import BaselineRepository, InterfaceUniverse
from ..foundation.environment import BrowserEnvironment
from ..foundation.errors import InvalidStateError, NetworkError
from ..foundation.lifecycle import ResourceScope
from ..foundation.tasks import TaskQueue
from ..foundation.urls import origin_string, origin_tuple
from ..web.bom.window import Window
from ..web.dom.model import Document
from ..web.network.backend import CronetNetworkBackend
from ..web.network.cookies import CookieJar
from .bootstrap import bootstrap_runtime
from .lifecycle import DocumentLifecycle
from .navigation import NavigationController
from .realms import PageRealm
from .resource_loading import ResourceController
from .scripting import ScriptController
from .services import PageServices
from .state import Ownership


class _TaskHostAdapter:
    """Minimal host surface handed to the page's TaskQueue.

    ``TaskQueue`` (foundation) may only ask its host the two TaskHost
    questions.  Holding a ``Page`` there would let foundation reach the
    whole platform object, so the queue receives this adapter instead: its
    only members are the protocol ones.
    """

    def __init__(self, page):
        self._page = page

    def is_closed(self):
        return self._page.closed

    def parser_blocked(self):
        return self._page.navigation.parser_is_blocked()


class _ContextBootstrapProxy:
    """Placeholder published while native Context installs templates."""

    def __init__(self):
        self.args = ()
        self.kw = {}

    def expose(self, *_args, **_kwargs):
        return None

    def exec_js(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


class Page:
    def __init__(self, context, url="about:blank", *, trace=None, environment=None, parent=None, origin=None):
        self.context_owner = context
        self.parent_page = parent
        self.top_page = parent.top_page if parent is not None else self
        self.origin = tuple(origin) if origin is not None else origin_tuple(
            url, inherited=getattr(parent, "origin", None)
        )
        self.origin = context._blob_origins.get(str(url), self.origin)
        self.environment = context.browser.environment.overlay(environment)
        self.resource_interceptor = context.browser.resource_interceptor
        # A page can override the browser default without changing the
        # configuration used by other pages in the same browser.
        self._trace = context.browser.trace if trace is None else bool(trace)
        self.closed = False
        self._resource_scope = ResourceScope()
        self._close_lock = threading.Lock()
        self._closing = False
        self.navigation = NavigationController(self)
        self.lifecycle = DocumentLifecycle(self)
        self.scripts = ScriptController(self)
        self.resources = ResourceController(self)
        # The task queue lives in foundation and may only ask its host the two
        # TaskHost questions; hand it a narrow adapter instead of this Page.
        self.tasks = TaskQueue(_TaskHostAdapter(self))
        # Task exceptions are currently invisible: surface them the way a
        # browser does, as an ErrorEvent on the realm's window.
        self._reporting_task_error = False
        self.tasks.set_error_sink(self._report_task_error)
        self._owned = Ownership()
        # Web-facing objects never see this Page; they receive the narrow
        # PageServices surface assembled from the controllers above.
        self.services = PageServices(
            context, self.environment, self.trace, self._owned,
            self.navigation, self.scripts, self.resources, self.tasks,
            self._resource_scope,
        )
        self.services._origin = self.origin
        self.document = Document(services=self.services, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.resource_interceptor = context.browser.resource_interceptor
        self.cache_enabled = context.browser.cache_enabled
        self.network_backend = context.network_backend
        self.window = Window(
            services=self.services,
            feature_runtime=context.browser.interface_universe.runtime,
        )
        self.services.window = self.window
        self.services.document = self.document
        self.window.location.href = str(url)
        # Native template installation can inspect the global object before
        # Context.__init__ returns.  Publish a sentinel so Window callbacks
        # never observe a partially initialized Page attribute.
        self.context = _ContextBootstrapProxy()
        self._devtools_target = None
        self.realm = PageRealm(self)
        self.services.bind_realm(self.realm)
        self.realm.executor.attach_task_queue(self.tasks)
        self.context = self.realm.context
        Window._bind_context(self.context, self.window)
        if parent is not None:
            self.window._set_frame_parent(parent.window)

    @property
    def trace(self):
        return self._trace

    @trace.setter
    def trace(self, enabled):
        if not isinstance(enabled, bool):
            raise TypeError("trace must be a bool")

        def update():
            if self.closed or self.context_owner.closed:
                raise RuntimeError("page is closed")
            self.context.trace = enabled
            self._trace = enabled
            self.services.trace = enabled

        self.realm.executor.call(update)

    def _update_origin(self, url):
        self.origin = origin_tuple(url, inherited=getattr(self.parent_page, "origin", None))
        self.origin = self.context_owner._blob_origins.get(str(url), self.origin)
        self.services._origin = self.origin
        self.window._origin = self.origin
        # The native access-check callback resolves the accessing realm from
        # this table.  Navigation can change the origin after the Context has
        # already been created, so keep the bound entry in sync.
        entry = Window._context_origins.get(id(self.context))
        if entry is not None:
            Window._context_origins[id(self.context)] = (entry[0], self.origin)

    # -- task error reporting --------------------------------------------
    # TaskQueue routes task exceptions here through its error sink so a faulty
    # timer or callback surfaces on the window instead of vanishing.

    def _report_task_error(self, error):
        """Mirror a task exception onto the realm as an ``error`` event."""
        if self.closed or self._reporting_task_error:
            return
        self._reporting_task_error = True
        try:
            self.lifecycle.dispatch_script_error(None, error)
        except BaseException:
            logger.debug("failed to report task error", exc_info=True)
        finally:
            self._reporting_task_error = False

    @property
    def binding_session(self):
        return self.realm.binding_session

    @property
    def url(self):
        return self.window.location.href

    @url.setter
    def url(self, value):
        self.goto(value)

    # -- public API ------------------------------------------------------
    # Everything else web-facing code needs is served by ``self.services``
    # (PageServices); controllers reach each other through the Page attributes
    # directly.  Only user-facing forwards remain here.

    def set_content(self, html, url="about:blank"):
        return self.realm.executor.call(self.navigation.set_content, html, url)

    def goto(self, url, *, timeout=30, wait_until="load"):
        return self.realm.executor.call(
            self.navigation.goto, url, timeout=timeout, wait_until=wait_until,
        )

    def evaluate(self, script, filename=None):
        return self.realm.executor.call(
            self.scripts.evaluate, script, filename=filename,
        )

    def run_until_idle(self, timeout=0):
        self.realm.executor.call(self.tasks.run_until_idle, timeout)

    def dispatch_event(self, event_type, *, target="window", selector=None, event_class=None, init=None):
        """Dispatch a host-trusted event and report whether it was not cancelled.

        ``target`` is ``"window"`` (default), ``"document"`` or a DOM node from
        :meth:`query_selector`; ``selector`` resolves the target from the current
        document instead.  ``event_class`` names the event interface to build
        (inferred from ``event_type`` when omitted) and ``init`` is a plain dict
        of EventInit members.
        """
        return self.realm.executor.call(
            self.lifecycle.dispatch_event, event_type,
            target=target, selector=selector, event_class=event_class, init=init,
        )

    def query_selector(self, selector):
        """Return the host-side DOM node matching a CSS selector, else ``None``."""
        def resolve():
            node = self.document.fn_querySelector(str(selector))
            return None if node is pyv8.Null else node

        return self.realm.executor.call(resolve)

    def set_visibility(self, hidden):
        """Switch the document visibility state and fire its lifecycle events."""
        return self.realm.executor.call(self.lifecycle.set_visibility, hidden)

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            # A concurrent close is already doing the owner-thread teardown;
            # leave the scope OPEN so a failed owner call can be retried.
            if self._closing:
                return
            self._closing = True
        # A paused inspector loop or a start-paused gate blocks the realm
        # owner. Wake it before synchronously asking that owner to perform
        # the teardown.
        if not self.realm.executor.is_owner_thread():
            target = self._devtools_target
            if target is not None:
                target.wake_waiting_owner()
        try:
            return self.realm.executor.call(self._close_owner)
        finally:
            with self._close_lock:
                self._closing = False
            # _close_owner closes the scope after all owner-thread cleanup.
            # If dispatch timed out before it started, retain OPEN state and
            # allow a subsequent close() to retry instead of losing resources.
            if self.closed:
                self._resource_scope.close()

    def _close_owner(self):
        if self.closed:
            return
        if self.navigation.has_navigation:
            self.lifecycle.dispatch_beforeunload()
            self.lifecycle.dispatch_page_hide(False)
            self.lifecycle.dispatch_unload()
        # Drop cached JS scripts before the V8 context goes away.
        self.scripts.clear_js_helpers()
        if self._devtools_target is not None:
            if self.context_owner._devtools is not None:
                self.context_owner._devtools.unregister(self)
            self._devtools_target = None
        self.closed = True
        self.tasks.close()
        self.resources.reset()
        self.lifecycle.load_waiters.clear()
        for frame in self._owned.drain("frames"):
            self.services.context_services.discard_frame(frame)
            frame.close()
        for ws in self._owned.drain("websockets"):
            self.services.context_services.discard_websocket(ws)
            if self.network_backend:
                self.network_backend.close_websocket(ws)
        for worker in self._owned.drain("workers"):
            self.services.worker_services.discard_worker(worker)
            worker.fn_terminate()
        self.context_owner._discard_page(self)
        Window._unbind_context(self.context)
        self.realm.close()
        parent_cleanup = getattr(self, "_parent_scope_cleanup", None)
        if callable(parent_cleanup):
            parent_cleanup()
            self._parent_scope_cleanup = None
        # Close callbacks that were not explicitly drained (for example,
        # asynchronous resource cancellation handles).  This is idempotent
        # and guarantees eventual cleanup even when close() was initiated by
        # another thread and its waiting call timed out.
        self._resource_scope.close()


class BrowserContext:
    def __init__(self, browser):
        self.browser, self.pages = browser, []
        self.cookies = CookieJar()
        self.resource_cache_dir = browser.cache_dir if browser.cache_enabled else None
        self.resource_cache_lock = threading.RLock()
        self.network_backend = CronetNetworkBackend(options=browser.cycronet_options) if browser.real_network else None
        if self.network_backend is not None:
            self.network_backend.cookies = self.cookies
        self._blob_urls = {}
        self._blob_origins = {}
        self.closed = False
        self._devtools = None

    def _ensure_open(self):
        if self.closed or self.browser._closed:
            raise InvalidStateError("BrowserContext is closed")

    def create_blob_url(self, blob, realm=None):
        """Mint a ``blob:`` URL whose origin is the creating realm's origin.

        The blob inherits the *creator* origin rather than one derived from the
        serialized URL: two opaque origins must stay distinct, so a fresh
        ``origin_tuple`` would make ``blob:null/...`` documents look unrelated
        to the page that created them.
        """
        self._ensure_open()
        url_source = "about:blank" if realm is None else getattr(realm, "url", "about:blank")
        url = "blob:" + origin_string(url_source) + "/" + str(uuid.uuid4())
        self._blob_urls[url] = (bytes(blob._data), blob.get_type())
        creator_origin = None if realm is None else getattr(realm, "origin", None)
        self._blob_origins[url] = creator_origin if creator_origin is not None else origin_tuple(url_source)
        return url

    def resolve_blob_url(self, url):
        self._ensure_open()
        if url not in self._blob_urls:
            raise NetworkError("revoked or unknown Blob URL")
        return self._blob_urls[url]

    def revoke_blob_url(self, url):
        self._ensure_open()
        self._blob_urls.pop(url, None)
        self._blob_origins.pop(url, None)

    # Context-level services exposed to realm objects.  Keeping these methods
    # here makes cookie/blob ownership explicit without exposing storage fields.
    @property
    def cookie_jar(self):
        return self.cookies

    def new_page(self, url="about:blank", *, trace=None, environment=None):
        self._ensure_open()
        page = Page(self, trace=trace, environment=environment)
        self.pages.append(page)
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

    def start_devtools(self, page: Page, *, options: pyv8.DevToolsOptions | None = None) -> pyv8.DevToolsHandle:
        self._ensure_open()
        if page not in self.pages or page.closed:
            raise InvalidStateError("Page does not belong to this BrowserContext")
        if self._devtools is not None:
            raise InvalidStateError("BrowserContext already has DevTools attached")
        from .devtools import start_devtools
        return start_devtools(self, page, options=options)

    def close(self):
        if self.closed:
            return
        self.closed = True
        first_error = None
        for page in list(self.pages):
            try:
                page.close()
            except BaseException as exc:
                # Teardown is best effort across independent pages.  Do not
                # leave later pages, DevTools or the network backend alive
                # merely because one realm reported an error.
                if first_error is None:
                    first_error = exc
        self.pages.clear()
        if self._devtools is not None:
            self._devtools.close()
            self._devtools = None
        self._blob_urls.clear()
        self._blob_origins.clear()
        if self.network_backend:
            self.network_backend.close()
            self.network_backend = None
        if first_error is not None:
            raise first_error


class Browser:
    def __init__(self, *,
                 environment=None,
                 real_network=False, resource_loader=None, cycronet_options=None, resource_interceptor=None,
                 trace=False, trace_callback=None, traceable=False,
                 cache=True, cache_dir=None,
                 expose_unimplemented=True,
                 stub_logging="info", interface_baseline_path=None):
        if trace_callback is not None and not callable(trace_callback):
            raise TypeError("trace_callback must be callable or None")
        self.trace_callback = trace_callback
        started = time.perf_counter_ns()
        self._closed = False
        # pyv8 initializes its process-global V8 runtime during package import.
        self.contexts = []
        self.resource_loader = resource_loader
        self.resource_interceptor = resource_interceptor
        self.cache_enabled = bool(cache)
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        self.real_network = bool(real_network)
        self.cycronet_options = dict(cycronet_options or {})
        self.trace = bool(trace)
        self.traceable = bool(traceable) or self.trace
        self.environment = BrowserEnvironment.from_value(environment)
        self.expose_unimplemented = bool(expose_unimplemented)
        self.stub_logging = str(stub_logging or "info")
        self._startup_stats = {}
        snapshot = BaselineRepository.load(interface_baseline_path)
        loaded = time.perf_counter_ns()
        self.interface_universe = InterfaceUniverse(
            snapshot, runtime=bootstrap_runtime(),
            stub_logging=self.stub_logging,
            expose_unimplemented=self.expose_unimplemented,
        )
        validation_finished = validation_started = loaded
        self.interface_baseline_path = snapshot.path
        self.interface_baseline_info = snapshot.info
        self._startup_stats.update(BaselineRepository._last_timings)
        self._startup_stats.update({
            "validation_ms": (validation_finished - validation_started) / 1_000_000,
            "browser_init_ms": (validation_finished - started) / 1_000_000,
            "known_interfaces": len(self.interface_universe.interface_names("Window")),
            "core_interfaces": len(self.interface_universe.eager_names("Window")),
            "deferred_interfaces": max(0, len(self.interface_universe.interface_names("Window")) - len(self.interface_universe.eager_names("Window"))),
            "materialized_interfaces": 0,
            "binding_assembly_ms": 0.0,
            "context_create_ms": 0.0,
            "deferred_global_declare_ms": 0.0,
            "deferred_globals": 0,
            "deferred_materialized": 0,
        })

    @property
    def startup_stats(self):
        return MappingProxyType(dict(self._startup_stats))

    def _record_startup(self, **values):
        if os.environ.get("PYV8C_STARTUP_STATS", "1") != "0":
            self._startup_stats.update(values)

    def new_context(self):
        if self._closed:
            raise InvalidStateError("Browser is closed")
        context = BrowserContext(self)
        self.contexts.append(context)
        return context

    def close(self):
        if self._closed:
            return
        first_error = None
        for context in self.contexts:
            try:
                context.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        self.contexts.clear()
        self._closed = True
        if first_error is not None:
            raise first_error
