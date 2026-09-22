"""Platform-owned V8 realms for pages and workers."""

import time

import pyv8

from ..engine.trace import log_trace
from ..foundation.executor import RealmExecutor
from ..foundation.scripts import RealmScripts
from ..foundation.tasks import TaskQueue
from ..web.bom.chrome import install_window_chrome
from .services import WorkerRealmServices


def _context_kwargs(session, context_type, trace, on_materialize=None, trace_callback=None, traceable=False, exposure_type=None):
    """Shared native Context arguments assembled from one binding session."""
    return {
        "trace": trace,
        "traceable": traceable,
        "trace_callback": (log_trace if trace_callback is None else trace_callback) if trace or traceable else None,
        "ctx_type": context_type,
        "class_configs": session.configs,
        "lazy_factory": session.lazy_factory(exposure_type or context_type, on_materialize=on_materialize),
    }


class _RealmTaskHost:
    """The only two questions a worker's task queue may ask its realm.

    Mirrors the adapter a page hands to its own queue: the queue lives in
    ``foundation`` and must never reach the realm object itself.
    """

    def __init__(self, realm):
        self._realm = realm

    def is_closed(self):
        return self._realm.is_closed()

    def parser_blocked(self):
        # A worker has no parser to gate its task sources on.
        return False


def _declare_realm_interfaces(context, session, *, context_type, universe):
    """Expose eager interfaces and declare lazy names on a fresh context."""
    for name in universe.eager_names(context_type):
        context.expose(session.class_for(name), name=name)
    return context.declare_lazy(
        session.lazy_names(context_type),
        skip=universe.eager_names(context_type),
    )


class WorkerRealm:
    """Own the worker isolate, context, task queue and binding session."""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.isolate = None
        self.context = None
        self.scope = None
        self.scripts = None
        # A worker global schedules like a window does: it owns its task queue
        # and drives it from its own owner thread, so timers, microtasks and
        # posted messages run there rather than on the parent page.
        self.tasks = TaskQueue(_RealmTaskHost(self))
        self.services = WorkerRealmServices(self.tasks, self)

    def is_closed(self):
        return self.isolate is None

    def initialize(self, scope):
        self.scope = scope
        config = self.config
        session = config.session
        session.class_configs("Worker", roots=(scope.__idl_spec__.name,))
        self.isolate = pyv8.Isolate()
        self.context = self.isolate.context(
            scope, **_context_kwargs(session, "Worker", config.trace,
                                     trace_callback=config.browser.trace_callback,
                                     traceable=config.browser.traceable),
        )
        session.attach_context(self.context)
        _declare_realm_interfaces(
            self.context, session,
            context_type="Worker",
            universe=config.universe,
        )
        self.scripts = RealmScripts(self.context)
        return self.context

    # -- realm scripting -------------------------------------------------
    # The worker's global scope reaches these through WorkerRealmServices,
    # exactly as a page reaches its own through PageServices.

    def evaluate_script(self, source):
        return self.context.exec_js(source)

    def enqueue_microtask(self, callback):
        return self.scripts.enqueue_microtask(callback)

    def make_promise(self):
        return self.scripts.make_promise()

    def js_error_value(self, exc):
        return self.scripts.js_error_value(exc)

    def promise_resolve(self, value):
        return self.scripts.promise_resolve(value)

    def close(self):
        first_error = None

        def attempt(callback):
            nonlocal first_error
            try:
                callback()
                return True
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                return False

        attempt(self.tasks.close)
        if self.context is not None and attempt(self.context.close):
            self.context = None
        if self.scripts is not None and attempt(self.scripts.close):
            self.scripts = None
        attempt(self.config.session.detach_context)
        if self.context is None:
            self.scope = None
        if self.isolate is not None and attempt(self.isolate.close):
            self.isolate = None
        if first_error is not None:
            raise first_error


class PageRealm:
    """Own the V8 context and binding session for one page."""

    def __init__(self, page):
        self.page = page
        self.context = None
        self.executor = RealmExecutor(name="pyv8c-page")
        #: Compiled JavaScript helpers, created with the context in _initialize.
        self.scripts = None
        browser = page.context_owner.browser
        # Web objects constructed through this session receive PageServices,
        # never the platform Page itself.
        self.binding_session = browser.interface_universe.binding_session(page.services)
        page.services.bind_realm(self)
        try:
            if pyv8.current_context() is not None:
                # Asked for from inside a running script, where the caller already
                # holds what the realm's own thread would wait on: build it here.
                # An inserted frame is the case -- its window has to exist before
                # the script that inserted it returns.
                self._initialize()
            else:
                self.executor.call(self._initialize)
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        page = self.page
        browser = page.context_owner.browser
        binding_started = time.perf_counter_ns()
        self.binding_session.class_configs(
            "Window", roots=self.binding_session.universe.eager_names("Window"),
        )
        binding_prepared = time.perf_counter_ns()
        #: A frame's realm is not the top page's: the label says which it is.
        context_type = "FRAME" if page.parent_page is not None else "TOP"
        self.context = pyv8.Context(page.window, **_context_kwargs(
            self.binding_session, context_type, page.trace,
            exposure_type="Window",
            on_materialize=self._record_deferred_materialized,
            trace_callback=browser.trace_callback,
            traceable=browser.traceable,
        ))
        self.binding_session.attach_context(self.context)
        self.executor.attach_deadline_source(self.context)
        context_created = time.perf_counter_ns()
        deferred_globals = _declare_realm_interfaces(
            self.context, self.binding_session,
            context_type="Window",
            universe=browser.interface_universe,
        )
        deferred_declared_at = time.perf_counter_ns()
        # Chromium installs window.chrome before any page script runs.
        install_window_chrome(self.context, page.services)
        page.window._publish_name()
        chrome_installed_at = time.perf_counter_ns()
        # The realm owns the compiled JavaScript helpers, and the page's script
        # controller borrows this instance, so the isolate's error hooks and
        # every Web API helper share one set.  The hooks are what make an
        # exception V8 raises on its own -- inside a microtask job, which never
        # returns to a Python frame -- reach the page's error handlers instead
        # of stopping at the host's stderr.
        self.scripts = RealmScripts(self.context)
        self.scripts.install_error_hooks(
            page.report_uncaught_message, page.rejections.record,
        )
        browser._record_startup(
            binding_assembly_ms=(binding_prepared - binding_started) / 1_000_000,
            context_create_ms=(context_created - binding_prepared) / 1_000_000,
            deferred_global_declare_ms=(deferred_declared_at - context_created) / 1_000_000,
            chrome_install_ms=(chrome_installed_at - deferred_declared_at) / 1_000_000,
            core_interfaces=len(self.binding_session.materialized_interfaces),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            known_interfaces=len(self.binding_session.interface_names("Window")),
            deferred_globals=deferred_globals,
        )

    def _record_deferred_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        self.page.context_owner.browser._record_startup(
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            deferred_materialized=len(self.binding_session.materialized_interfaces)
            - len(self.binding_session.universe.eager_names("Window")),
        )

    def close(self):
        def close_context():
            context = self.context
            close = getattr(context, "close", None)
            if callable(close):
                close()
            self.context = None
            self.binding_session.detach_context()
            if self.scripts is not None:
                self.scripts.close()
                self.scripts = None
        if self.executor.closed:
            return
        self.executor.call(close_context)
        self.executor.close()


__all__ = ["PageRealm", "WorkerRealm"]
