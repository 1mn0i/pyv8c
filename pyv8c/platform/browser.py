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
import pyv8  # patch target for native Context instrumentation tests
from loguru import logger

from ..engine.universe import BaselineRepository, InterfaceUniverse
from ..foundation.arguments import is_nullish
from ..foundation.environment import BrowserEnvironment
from ..foundation.errors import InvalidStateError, NetworkError
from ..foundation.lifecycle import ResourceScope
from ..foundation.rejections import RejectionTracker
from ..foundation.tasks import TaskQueue
from ..foundation.urls import origin_string, origin_tuple
from ..web.bom.window import Window
from ..web.dom.document import Document
from ..web.network.backend import NetworkBackend
from ..web.network.cookies import CookieJar
from ..web.network.registry import create_transport
from .bootstrap import bootstrap_runtime
from .lifecycle import DocumentLifecycle
from .navigation import NavigationController
from .net import PageNet
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
        # Uncaught exceptions are currently invisible: surface them the way a
        # browser does, as an ErrorEvent on the realm's window.
        self._reporting_error = False
        self.tasks.set_error_sink(self.report_exception)
        # V8 reports a rejection before the turn that caused it ends, so the
        # candidates wait for the executor's next boundary to be published.
        self.rejections = RejectionTracker(self.report_rejection)
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
        # Host-side HTTP for this page: carries its cookies and headers through
        # the context's transport (see platform/net.py).
        self.net = PageNet(self)
        # Native template installation can inspect the global object before
        # Context.__init__ returns.  Publish a sentinel so Window callbacks
        # never observe a partially initialized Page attribute.
        self.context = _ContextBootstrapProxy()
        self._devtools_target = None
        self.realm = PageRealm(self)
        self.services.bind_realm(self.realm)
        self.realm.executor.attach_task_queue(self.tasks)
        self.realm.executor.attach_rejection_tracker(self.rejections)
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

    # -- error reporting ---------------------------------------------------
    # One entry point for everything that notices an exception: TaskQueue routes
    # a task that raised here through its error sink, the DOM reports a listener
    # that threw, and the isolate's hooks report what V8 ran without returning
    # to Python at all.  All of them surface on the window as an ``error`` event
    # instead of vanishing.

    def report_exception(self, error=None, *, message=None, filename="", line=0,
                         column=0, value=None):
        """Mirror an uncaught exception onto the realm's window.

        Either a Python *error*, which brings its own text and (usually) the
        JavaScript value behind it, or -- for an exception that never reached
        Python -- the *message*, position and thrown *value* V8 captured.

        The guard keeps a page handler that throws while handling this from
        re-entering the reporter.
        """
        if self.closed or self._reporting_error:
            return
        self._reporting_error = True
        try:
            self.lifecycle.dispatch_script_error(
                None, error, message=message, filename=filename, line=line,
                column=column, value=value,
            )
        except BaseException:  # reporting a script error must not raise
            logger.debug("failed to report an exception", exc_info=True)
        finally:
            self._reporting_error = False

    def report_uncaught_message(self, message, source, line, column, error):
        """Report the pieces of an exception V8 raised on its own.

        This is the isolate's uncaught-exception hook: a microtask job that
        threw never returns to a Python frame, so V8 hands over the message, the
        throw site and the exception value it captured.
        """
        if is_nullish(source):
            source = ""
        self.report_exception(
            message=str(message), filename=str(source), line=int(line),
            column=int(column), value=error,
        )

    def report_rejection(self, event_type, promise, reason):
        """Mirror a rejection notice onto the window's rejection events."""
        if self.closed or self._reporting_error:
            return
        self._reporting_error = True
        try:
            self.lifecycle.dispatch_rejection(event_type, promise, reason)
        except BaseException:  # reporting a rejection must not raise
            logger.debug("failed to report a rejection", exc_info=True)
        finally:
            self._reporting_error = False

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
        def evaluate_data():
            value = self.scripts.evaluate(script, filename=filename)
            return value.to_python() if isinstance(value, pyv8.JSObject) else value
        return self.realm.executor.call(evaluate_data)

    def evaluate_handle(self, script, filename=None):
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
        first_error = None

        def attempt(callback, *args):
            nonlocal first_error
            try:
                callback(*args)
                return True
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                return False

        if not getattr(self, "_unload_dispatched", False):
            self._unload_dispatched = True
            if self.navigation.has_navigation:
                attempt(self.lifecycle.dispatch_beforeunload)
                attempt(self.lifecycle.dispatch_page_hide, False)
                attempt(self.lifecycle.dispatch_unload)
        # Drop cached JS scripts before the V8 context goes away.
        self.scripts.clear_js_helpers()
        self.scripts.reset()
        if self._devtools_target is not None:
            if self.context_owner._devtools is not None:
                if attempt(self.context_owner._devtools.unregister, self):
                    self._devtools_target = None
            else:
                self._devtools_target = None
        attempt(self.net.close)
        attempt(self.tasks.close)
        attempt(self.resources.reset)
        self.rejections.close()
        self.lifecycle.load_waiters.clear()
        for frame in self._owned.drain("frames"):
            if attempt(frame.close):
                self.services.context_services.discard_frame(frame)
            else:
                self._owned.track("frames", frame)
        for ws in self._owned.drain("websockets"):
            if not self.network_backend or attempt(self.network_backend.close_websocket, ws):
                self.services.context_services.discard_websocket(ws)
            else:
                self._owned.track("websockets", ws)
        for worker in self._owned.drain("workers"):
            if attempt(worker.fn_terminate):
                self.services.worker_services.discard_worker(worker)
            else:
                self._owned.track("workers", worker)
        if first_error is not None:
            # Keep the realm owner available to retry failed native resources.
            raise first_error
        Window._unbind_context(self.context)
        self.realm.close()
        self.context = None
        self.closed = True
        self.context_owner._discard_page(self)
        parent_cleanup = getattr(self, "_parent_scope_cleanup", None)
        if callable(parent_cleanup):
            parent_cleanup()
            self._parent_scope_cleanup = None
        # Close callbacks that were not explicitly drained (for example,
        # asynchronous resource cancellation handles).  This is idempotent
        # and guarantees eventual cleanup even when close() was initiated by
        # another thread and its waiting call timed out.
        self._resource_scope.close()


#: The engines ``real_network=True`` assembles for whichever role was not named.
#: Both are ordinary engines: naming a role replaces its default, and naming
#: neither role -- with no networking asked for -- leaves both empty.
DEFAULT_HTTP_ENGINE = "curl_cffi"
DEFAULT_WS_ENGINE = "websockets"


def _options(browser, role):
    """The options the *role* engine is handed; a copy, so callers keep theirs.

    ``None`` rather than an empty dictionary when nothing was configured, which
    is the shape a registered engine is documented to be called with -- it lets
    a host tell "no options" from "options, all of them defaults" if it cares.
    """
    return dict(getattr(browser, f"{role}_options")) or None


def _roles(candidate):
    """Which roles an engine object serves, decided by what it can do."""
    return (
        candidate if hasattr(candidate, "request_sync") else None,
        candidate if hasattr(candidate, "websocket") else None,
    )


def _resolve_injected(browser, value):
    """Resolve ``network_backend=`` into the roles the given object serves.

    A bare callable is a factory and is called with no arguments.  Which roles
    the result fills is what it can do -- ``request_sync`` makes it the HTTP
    engine, ``websocket`` the socket engine -- so an engine for one role leaves
    the other to ``network_engine`` and ``ws_engine``.

    Options for a role an injected object fills are refused: it was built before
    the context existed, so nothing would read them.
    """
    candidate = value
    if not any(_roles(candidate)) and callable(candidate):
        candidate = candidate()
    http, ws = _roles(candidate)
    if http is None and ws is None:
        raise TypeError(
            "network_backend must be an engine name, an engine instance with "
            "request_sync or websocket, or a factory"
        )
    for role, filled in (("http", http is not None), ("ws", ws is not None)):
        if filled and getattr(browser, f"{role}_options"):
            named = "network_engine" if role == "http" else "ws_engine"
            raise TypeError(
                f"{role}_options cannot configure an injected network_backend; "
                f"name the engine with {named} to pass options to it"
            )
    return http, ws


def _refuse_unconsumed_options(browser):
    """Reject options no engine would read, instead of dropping them quietly."""
    given = [role for role in ("http", "ws") if getattr(browser, f"{role}_options")]
    if given:
        listed = " and ".join(f"{role}_options" for role in given)
        raise TypeError(
            f"{listed} was given but no network engine is configured; "
            "set real_network=True, network_engine or ws_engine"
        )


def _refuse_shared_options(browser, name):
    """One engine cannot be configured twice; say which options to move."""
    if browser.ws_options:
        raise TypeError(
            f"network_engine and ws_engine both name {name!r}, which is built once, "
            "so ws_options would be ignored; give the WebSocket role its own engine "
            "or move those settings into http_options"
        )


def _build_network_backend(browser):
    """Assemble one context's HTTP engine and its WebSocket engine.

    ``network_backend`` wins for the roles it can serve.  Otherwise
    ``network_engine`` names the HTTP engine and ``ws_engine`` the socket engine,
    while ``real_network=True`` fills in the default for whichever role was left
    unnamed -- so naming one role assembles exactly that role.

    Each role's options are checked against the engine that will read them, so a
    key it does not accept is reported here instead of reaching a client that
    would ignore it, and options with no engine to read them are refused rather
    than dropped.
    """
    http = ws = None
    shared = None
    injected = browser.network_backend
    if isinstance(injected, str):
        # A name here has always meant "the HTTP engine to use for this context".
        http = create_transport(injected, options=_options(browser, "http"), strict=True)
        shared = injected if injected == browser.ws_engine else None
    elif injected is not None:
        http, ws = _resolve_injected(browser, injected)

    http_name, ws_name = browser.network_engine, browser.ws_engine
    if http is None and http_name is None and browser.real_network:
        http_name = DEFAULT_HTTP_ENGINE
    if ws is None and ws_name is None and browser.real_network:
        ws_name = DEFAULT_WS_ENGINE

    if http is None and http_name is not None:
        http = create_transport(http_name, options=_options(browser, "http"), strict=True)
    if ws is None and ws_name is not None:
        if http is not None and ws_name in (http_name, shared):
            # One name, one engine: building it twice would mean two clients,
            # two pools and two connection caches for the same context.
            _refuse_shared_options(browser, ws_name)
            ws = http
        else:
            ws = create_transport(ws_name, kind="ws", options=_options(browser, "ws"), strict=True)

    if http is None and ws is None:
        _refuse_unconsumed_options(browser)
        return None
    return NetworkBackend(http, ws)


class BrowserContext:
    def __init__(self, browser):
        self.browser, self.pages = browser, []
        self.cookies = CookieJar()
        self.resource_cache_dir = browser.cache_dir if browser.cache_enabled else None
        self.resource_cache_lock = threading.RLock()
        self.network_backend = _build_network_backend(browser)
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
        first_error = None
        for page in list(self.pages):
            try:
                page.close()
            except BaseException as exc:  # the reason is the comment below
                # Teardown is best effort across independent pages.  Do not
                # leave later pages, DevTools or the network backend alive
                # merely because one realm reported an error.
                if first_error is None:
                    first_error = exc
        for name in ("_devtools", "network_backend"):
            resource = getattr(self, name)
            if resource is not None:
                try:
                    resource.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    setattr(self, name, None)
        self._blob_urls.clear()
        self._blob_origins.clear()
        if first_error is not None:
            raise first_error
        self.closed = True
        if self in self.browser.contexts:
            self.browser.contexts.remove(self)


class Browser:
    def __init__(self, *,
                 environment=None,
                 real_network=False, resource_loader=None, resource_interceptor=None,
                 network_engine=None, ws_engine=None, network_backend=None,
                 http_options=None, ws_options=None, default_headers=None,
                 trace=False, trace_callback=None, traceable=False,
                 cache=True, cache_dir=None,
                 expose_unimplemented=True,
                 stub_logging="info", interface_baseline_path=None):
        if trace_callback is not None and not callable(trace_callback):
            raise TypeError("trace_callback must be callable or None")
        self.trace_callback = trace_callback
        started = time.perf_counter_ns()
        self._closed = False
        # pyv8 starts its process-global V8 runtime on first use rather than at
        # import, so the first realm created below carries that startup cost.
        self.contexts = []
        self.resource_loader = resource_loader
        self.resource_interceptor = resource_interceptor
        self.cache_enabled = bool(cache)
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        # A transport is built when one was asked for: either through
        # ``real_network`` or by naming or injecting an engine directly.
        #: Whether a context should assemble the default engines for whichever
        #: role was not named; naming a role assembles exactly that role.
        self.real_network = bool(real_network)
        self.network_engine = None if network_engine is None else str(network_engine)
        self.ws_engine = None if ws_engine is None else str(ws_engine)
        self.network_backend = network_backend
        #: Options for the HTTP engine and for the WebSocket engine.  Each engine
        #: says which keys it accepts and a context refuses the rest, so a
        #: misspelt key is reported when the context is created, not passed on
        #: unread.
        self.http_options = dict(http_options or {})
        self.ws_options = dict(ws_options or {})
        #: Headers every host request starts from, on top of the ones derived
        #: from the browser environment (see ``platform.net.PageNet``).
        self.default_headers = dict(default_headers or {})
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
        for context in list(self.contexts):
            try:
                context.close()
            except BaseException as exc:  # every context still gets its close attempt
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        self._closed = True
