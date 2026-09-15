"""Platform-owned V8 realms for pages and workers."""

import time

import pyv8

from ..engine.trace import log_trace
from ..foundation.executor import RealmExecutor
from ..web.bom.chrome import install_window_chrome


def _context_kwargs(session, context_type, trace, on_materialize=None, trace_callback=None, traceable=False):
    """Shared native Context arguments assembled from one binding session."""
    return {
        "trace": trace,
        "preserve_arguments": True,
        "traceable": traceable,
        "trace_callback": (log_trace if trace_callback is None else trace_callback) if trace or traceable else None,
        "ctx_type": context_type,
        "class_configs": session.configs,
        "lazy_factory": session.lazy_factory(context_type, on_materialize=on_materialize),
    }


def _declare_realm_interfaces(context, session, *, context_type, universe):
    """Expose eager interfaces and declare lazy names on a fresh context."""
    for name in universe.eager_names(context_type):
        context.expose(session.class_for(name), name=name)
    return context.declare_lazy(
        session.lazy_names(context_type),
        skip=universe.eager_names(context_type),
    )


class WorkerRealm:
    """Own the worker isolate, context, and binding session."""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.isolate = None
        self.context = None
        self.scope = None

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
        return self.context

    def close(self):
        context = self.context
        self.context = None
        self.scope = None
        if context is not None:
            del context
        isolate = self.isolate
        self.isolate = None
        if isolate is not None:
            try:
                isolate.close()
            except RuntimeError:
                pass


class PageRealm:
    """Own the V8 context and binding session for one page."""

    def __init__(self, page):
        self.page = page
        self.executor = RealmExecutor(name="pyv8c-page")
        browser = page.context_owner.browser
        # Web objects constructed through this session receive PageServices,
        # never the platform Page itself.
        self.binding_session = browser.interface_universe.binding_session(page.services)
        page.services.bind_realm(self)
        self.executor.call(self._initialize)

    def _initialize(self):
        page = self.page
        browser = page.context_owner.browser
        binding_started = time.perf_counter_ns()
        self.binding_session.class_configs(
            "Window", roots=self.binding_session.universe.eager_names("Window"),
        )
        binding_prepared = time.perf_counter_ns()
        self.context = pyv8.Context(page.window, **_context_kwargs(
            self.binding_session, "Window", page.trace,
            on_materialize=self._record_deferred_materialized,
            trace_callback=browser.trace_callback,
            traceable=browser.traceable,
        ))
        self.binding_session.attach_context(self.context)
        context_created = time.perf_counter_ns()
        deferred_globals = _declare_realm_interfaces(
            self.context, self.binding_session,
            context_type="Window",
            universe=browser.interface_universe,
        )
        deferred_declared_at = time.perf_counter_ns()
        # Chromium installs window.chrome before any page script runs.
        install_window_chrome(self.context, page.services)
        chrome_installed_at = time.perf_counter_ns()
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
            self.context = None
            close = getattr(context, "close", None)
            if callable(close):
                close()
        if self.executor.closed:
            return
        try:
            self.executor.call(close_context)
        finally:
            self.executor.close()


__all__ = ["PageRealm", "WorkerRealm"]
