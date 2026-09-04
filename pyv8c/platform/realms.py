"""Platform-owned V8 realms for pages and workers."""

import pyv8
import time

from ..engine.registry import expose_interfaces
from ..foundation.executor import RealmExecutor


def _context_kwargs(session, context_type, hook, on_materialize=None):
    """Shared native Context arguments assembled from one binding session."""
    return {
        "hook": hook,
        "ctx_type": context_type,
        "class_configs": session.configs,
        "lazy_factory": session.lazy_factory(context_type, on_materialize=on_materialize),
    }


def _declare_realm_interfaces(context, session, *, context_type, generate_stubs, universe):
    """Expose eager interfaces and declare lazy names on a fresh context."""
    expose_interfaces(
        context,
        context_type=context_type,
        generate_stubs=generate_stubs,
        universe=universe,
        class_for=session.class_for,
    )
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
        self.isolate = pyv8.Isolate()
        self.context = self.isolate.context(
            scope, **_context_kwargs(session, "Worker", config.hook),
        )
        session.attach_context(self.context)
        _declare_realm_interfaces(
            self.context, session,
            context_type="Worker",
            generate_stubs=config.generate_stubs,
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
            self.binding_session, "Window", page.hook,
            on_materialize=self._record_lazy_materialized,
        ))
        self.binding_session.attach_context(self.context)
        if page.hook:
            self.context.exec_js("faker.hook=true;")
        context_created = time.perf_counter_ns()
        lazy_registered = _declare_realm_interfaces(
            self.context, self.binding_session,
            context_type="Window",
            generate_stubs=browser.generate_interface_stubs,
            universe=browser.interface_universe,
        )
        lazy_registered_at = time.perf_counter_ns()
        browser._record_startup(
            binding_plan_ms=(binding_prepared - binding_started) / 1_000_000,
            context_create_ms=(context_created - binding_prepared) / 1_000_000,
            lazy_register_ms=(lazy_registered_at - context_created) / 1_000_000,
            eager_interfaces=len(self.binding_session.materialized_interfaces),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            interface_names=len(self.binding_session.interface_names("Window")),
            lazy_registered=lazy_registered,
        )

    def _record_lazy_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        self.page.context_owner.browser._record_startup(
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            lazy_materialized=len(self.binding_session.materialized_interfaces)
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
