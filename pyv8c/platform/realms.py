"""Platform-owned V8 realms for pages and workers."""

import pyv8
import time

from ..engine.registry import expose_interfaces


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
        context_kwargs = {
            "hook": config.hook,
            "ctx_type": "Worker",
            "class_configs": session.configs,
            "lazy_factory": session.lazy_factory("Worker"),
        }
        self.context = self.isolate.context(scope, **context_kwargs)
        expose_interfaces(
            self.context,
            context_type="Worker",
            generate_stubs=config.generate_stubs,
            universe=config.universe,
            class_for=session.class_for,
        )
        self.context.declare_lazy(
            session.lazy_names("Worker"),
            skip=config.universe.eager_names("Worker"),
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
        browser = page.context_owner.browser
        # Web objects constructed through this session receive PageServices,
        # never the platform Page itself.
        self.binding_session = browser.interface_universe.binding_session(page.services)
        binding_started = time.perf_counter_ns()
        self.binding_session.class_configs(
            "Window", roots=self.binding_session.universe.eager_names("Window"),
        )
        binding_prepared = time.perf_counter_ns()
        context_kwargs = {
            "hook": page.hook,
            "ctx_type": "Window",
            "class_configs": self.binding_session.configs,
            "lazy_factory": self.binding_session.lazy_factory(
                "Window", on_materialize=self._record_lazy_materialized,
            ),
        }
        self.context = pyv8.Context(page.window, **context_kwargs)
        if page.hook:
            self.context.exec_js("faker.hook=true;")
        context_created = time.perf_counter_ns()
        browser = page.context_owner.browser
        expose_interfaces(
            self.context,
            context_type="Window",
            generate_stubs=browser.generate_interface_stubs,
            universe=browser.interface_universe,
            class_for=self.binding_session.class_for,
        )
        lazy_names = self.binding_session.lazy_names("Window")
        lazy_registered = self.context.declare_lazy(
            lazy_names, skip=self.binding_session.universe.eager_names("Window"),
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
        close_context = getattr(self.context, "close", None)
        if callable(close_context):
            close_context()


__all__ = ["PageRealm", "WorkerRealm"]
