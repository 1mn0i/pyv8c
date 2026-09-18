"""Platform-owned V8 realms for pages and workers."""

import time

import v8n

from ..engine.trace import trace_sink
from ..foundation.executor import RealmExecutor
from ..foundation.scripts import RealmScripts
from ..foundation.tasks import TaskQueue
from ..web.bom.chrome import install_window_chrome
from .services import WorkerRealmServices


def _create_realm(session, context_type, *, name, global_this, timeout=0, on_materialize=None):
    """Create one realm from one batch, and record what that batch installed.

    The batch is a construction parameter rather than something installed
    afterwards, because the global object's template has to exist before the
    context does.  Recording happens here, after the realm object exists: a
    realm that failed to be created must leave the session with nothing
    installed, so that building the description again is a retry rather than a
    second half of the same attempt.

    Everything the realm exposes but does not pre-build is built later, by the
    global object's interceptor; ``on_materialize`` is how the host hears about
    each one, which is what keeps its startup counters honest.
    """
    # The host object's own interface is a root of the batch, always.  The realm
    # is created around it -- the binding builds the global object from the
    # interface the host is an instance of -- and a context type's exposed names
    # need not include it: a worker's do not, because a worker global is not a
    # name a page reads.
    declaration = getattr(type(global_this), "__idl_spec__", None)
    roots = (declaration.name,) if getattr(declaration, "name", None) else ()
    batch = session.realm_description(context_type, roots=roots)
    realm = v8n.Realm(
        name=name,
        global_this=global_this,
        description=batch.description,
        timeout=timeout,
    )
    session.attach_realm(realm, batch.names, on_materialize=on_materialize)
    # Objects built in Python reach JavaScript through this realm, so the realm
    # is what asks the session to install an interface before an object of a new
    # type crosses; see BindingSession.prepare_interface.
    realm.set_type_resolver(session.prepare_interface)
    return realm, batch


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


class WorkerRealm:
    """Own the worker isolate, realm, task queue and binding session."""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.realm = None
        self.scripts = None
        # A worker global schedules like a window does: it owns its task queue
        # and drives it from its own owner thread, so timers, microtasks and
        # posted messages run there rather than on the parent page.
        self.tasks = TaskQueue(_RealmTaskHost(self))
        self.services = WorkerRealmServices(self.tasks, self)

    def is_closed(self):
        return self.realm is None

    def initialize(self, scope):
        config = self.config
        session = config.session
        self.realm, _ = _create_realm(
            session, "DedicatedWorker",
            name=f"pyv8c-worker:{getattr(scope, 'name', 'worker')}",
            global_this=scope,
        )
        if config.trace:
            self.realm.trace_hook = trace_sink(
                "Worker", config.browser.trace_callback,
            )
        self.scripts = RealmScripts(self.realm)
        return self.realm

    # -- realm scripting -------------------------------------------------
    # The worker's global scope reaches these through WorkerRealmServices,
    # exactly as a page reaches its own through PageServices.

    def evaluate_script(self, source):
        return self.realm.evaluate(source)

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
        if self.realm is not None and attempt(self.realm.close):
            self.realm = None
        if self.scripts is not None and attempt(self.scripts.close):
            self.scripts = None
        attempt(self.config.session.detach_realm)
        if first_error is not None:
            raise first_error


class PageRealm:
    """Own the V8 realm and binding session for one page."""

    def __init__(self, page):
        self.page = page
        self.context = None
        self.executor = RealmExecutor(name="pyv8c-page")
        #: Compiled JavaScript helpers, created with the realm in _initialize.
        self.scripts = None
        browser = page.context_owner.browser
        # Web objects constructed through this session receive PageServices,
        # never the platform Page itself.
        self.binding_session = browser.interface_universe.binding_session(page.services)
        page.services.bind_realm(self)
        try:
            self.executor.call(self._initialize)
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        page = self.page
        browser = page.context_owner.browser
        binding_started = time.perf_counter_ns()
        self.context, batch = _create_realm(
            self.binding_session, "Window",
            name=f"pyv8c-page:{page.url}",
            global_this=page.window,
            on_materialize=self._record_deferred_materialized,
        )
        context_created = time.perf_counter_ns()
        # Values the Window built before its realm existed can only have their
        # interfaces installed now, which is the first moment there is a realm to
        # install into.
        page.window.expose_interfaces()
        # Installed before the page's own objects, so a host callback that
        # throws during a later install is reported like any other.
        self._apply_trace(page.trace)
        # Chromium installs window.chrome before any page script runs.
        install_window_chrome(self.context, page.services)
        chrome_installed_at = time.perf_counter_ns()
        # The realm owns the compiled JavaScript helpers, and the page's script
        # controller borrows this instance, so the realm's error hooks and
        # every Web API helper share one set.  The hooks are what make an
        # exception V8 raises on its own -- inside a microtask job, which never
        # returns to a Python frame -- reach the page's error handlers instead
        # of stopping at the host's stderr.
        self.scripts = RealmScripts(self.context)
        self.scripts.install_error_hooks(
            page.report_uncaught, page.rejections.record,
        )
        browser._record_startup(
            binding_assembly_ms=0.0,
            context_create_ms=(context_created - binding_started) / 1_000_000,
            deferred_global_declare_ms=(chrome_installed_at - context_created) / 1_000_000,
            chrome_install_ms=0.0,
            core_interfaces=len(batch.names),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            known_interfaces=len(self.binding_session.interface_names("Window")),
            deferred_globals=max(
                0,
                len(self.binding_session.exposed_names("Window")) - len(batch.names),
            ),
        )

    def _apply_trace(self, enabled):
        """Install or remove this realm's trace hook.

        The binding has one switch -- a hook, or no hook -- so whether a page
        may be traced at all is the host's own gate: a browser that was not
        built with instrumentation refuses to turn it on rather than accepting
        a request it cannot honour.
        """
        if self.context is None:
            return
        if not enabled:
            self.context.trace_hook = None
            return
        browser = self.page.context_owner.browser
        if not browser.traceable:
            raise RuntimeError(
                "trace cannot be enabled unless the browser was built with traceable=True"
            )
        self.context.trace_hook = trace_sink("Window", browser.trace_callback)

    def set_trace(self, enabled):
        """Turn tracing on or off for this realm.  Owner thread only."""
        self._apply_trace(enabled)

    def _record_deferred_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        session = self.binding_session
        self.page.context_owner.browser._record_startup(
            materialized_interfaces=len(session.materialized_interfaces),
            deferred_materialized=len(session.materialized_interfaces)
            - len(session.universe.eager_names("Window")),
        )

    def close(self):
        def close_realm():
            realm = self.context
            close = getattr(realm, "close", None)
            if callable(close):
                close()
            self.context = None
            self.binding_session.detach_realm()
            if self.scripts is not None:
                self.scripts.close()
                self.scripts = None
        if self.executor.closed:
            return
        self.executor.call(close_realm)
        self.executor.close()


__all__ = ["PageRealm", "WorkerRealm"]
