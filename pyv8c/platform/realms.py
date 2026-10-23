"""Platform-owned V8 realms for pages and workers."""

import time

import pyv8

from pyv8.flag import V8Attribute

from ..engine.trace import log_trace
from ..foundation import promises
from ..foundation.lifecycle import ResourceScope
from .resources.ownership import ResourceOwnership
from ..foundation.tasks import TaskQueue, TaskSource
from ..foundation.agent_tasks import TaskContext
from ..foundation.rejections import RejectionTracker
from ..foundation.rejection_state import WeakPromiseState
from ..foundation.tracing import silent_trace
from ..web.bom.chrome import install_window_chrome
from ..web.bom.legacy_factories import install_legacy_factories
from .services import WorkerRealmServices
from .sessions import BindingSession


def _v8_context_kwargs(session, context_type, trace, on_materialize=None, trace_callback=None, traceable=False, exposure_type=None, isolate=None, reuse_global=None):
    """Shared native V8Context arguments assembled from one binding session.

    ``isolate`` and ``reuse_global`` go in only when the caller has one: the worker
    path calls ``Isolate.context()``, which takes its isolate from the receiver and
    carries no window across navigations.
    """
    kwargs = {
        "trace": trace,
        "traceable": traceable,
        "trace_callback": (log_trace if trace_callback is None else trace_callback) if trace or traceable else None,
        "ctx_type": context_type,
        "lazy_factory": session.lazy_factory(exposure_type or context_type, on_materialize=on_materialize),
    }
    if isolate is not None:
        kwargs["isolate"] = isolate
    if reuse_global is not None:
        kwargs["reuse_global"] = reuse_global
    return kwargs


class _RealmTaskHost:
    """The only two questions a worker's task queue may ask its realm.

    The adapter keeps ``foundation``'s queue from reaching the realm object.
    """

    def __init__(self, realm):
        self._realm = realm

    def is_closed(self):
        return self._realm.is_closed()

    def parser_blocked(self):
        # A worker has no parser to gate its task sources on.
        return False


def _publish_window_name(context, page):
    """Write the window's name as host work, so no trace event reports it."""
    with silent_trace(context):
        page.window._publish_name()


#: Names Chrome hides in a realm that is not cross-origin isolated: the engine hands
#: ``SharedArrayBuffer`` to every context, so such a realm takes it back.
_NON_ISOLATED_HIDDEN = ("SharedArrayBuffer",)


def _declare_realm_interfaces(context, *, context_type, bindings):
    """Declare the realm's plan; the realm answers each name itself."""
    plan = bindings.plan(context_type)
    declared = context.install(plan.installs)
    if context_type == "Window":
        # Reading a target is what hands the alias its value, and that read is host
        # work: Chromium installs a legacy alias with no script-visible lookup of the
        # interface it points at, so no probe is reported for one.
        with silent_trace(context):
            for alias, target in bindings.idl.window_aliases().items():
                if target in plan.installs:
                    context.define_global(alias, context.glob[target],
                                          attributes=V8Attribute.DontEnum)
    for name in _NON_ISOLATED_HIDDEN:
        context.hide_global(name)
    return declared


class WorkerRealm:
    """Own the worker isolate, context, task queue and binding session."""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        #: Written by the runtime that starts the worker; the realm only reads it.
        self.script_url = ""
        self.execution_deadline = None
        self.execution_ticket = None
        self.stop_event = None
        self.isolate = None
        self.v8_context = None
        self.scope = None
        self.scripts = None
        # A worker global owns its task queue and drives it from its own owner
        # thread, so timers, microtasks and posted messages run there.
        self.tasks = TaskQueue(_RealmTaskHost(self))
        self.tasks.set_task_context(TaskContext(self))
        self._resource_scope = ResourceScope()
        self.resource_ownership = ResourceOwnership(self._resource_scope)
        self.services = WorkerRealmServices(self.tasks, self)
        self.callback_boundary = None

    def is_closed(self):
        return self.isolate is None

    def attach_rendering(self, executor, invoke):
        from ..foundation.rendering import RenderingScheduler
        self._rendering = RenderingScheduler(frame_rate=self.config.browser.frame_rate)
        self._frame_invoke = invoke
        executor.attach_rendering(self._rendering)
        self._resource_scope.add(self._rendering.close)

    def animation_frame_lane(self):
        return self._rendering.add(
            invoke=self._frame_invoke, active=lambda: not self.tasks.closed,
            timestamp=self.services.realm_services.performance.clock.at,
            report_error=self.services.realm_services.report_exception)

    def initialize(self, scope):
        self.scope = scope
        config = self.config
        session = config.session
        session.prepare("Worker")
        self.isolate = pyv8.Isolate()
        self.microtasks = pyv8.MicrotaskQueue(self.isolate)
        self.v8_context = self.isolate.context(
            scope, microtask_queue=self.microtasks, **_v8_context_kwargs(session, "Worker", config.trace,
                                     trace_callback=config.browser.trace_callback,
                                     traceable=config.browser.traceable),
        )
        _declare_realm_interfaces(
            self.v8_context,
            context_type="Worker",
            bindings=config.bindings,
        )
        return self.v8_context

    # The worker's global scope reaches these through WorkerRealmServices, as a
    # page reaches its own through PageServices.  The realm owns the context, so
    # it is also what decides how a promise behaves once that context is gone.

    def evaluate_script(self, source):
        try:
            return self.v8_context.exec_js(source)
        finally:
            self.cleanup_after_callback()

    def cleanup_after_callback(self):
        if self.callback_boundary is not None:
            self.callback_boundary()
        elif self.v8_context is not None and pyv8.current_context(self.isolate) is None:
            self.microtasks.checkpoint()

    def enqueue_microtask(self, callback):
        return promises.enqueue_microtask(self.v8_context, callback)

    def make_promise(self):
        return promises.make_promise(self.v8_context)

    def promise_resolve(self, value):
        return promises.promise_resolve(self.v8_context, value)

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

        attempt(self._resource_scope.close)
        attempt(self.tasks.close)
        if self.v8_context is not None and attempt(self.v8_context.close):
            self.v8_context = None
        attempt(self.config.session.detach)
        if self.v8_context is None:
            self.scope = None
        if self.v8_context is None and getattr(self, "microtasks", None) is not None:
            if attempt(self.microtasks.close):
                self.microtasks = None
        if self.isolate is not None and attempt(self.isolate.close):
            self.isolate = None
        if first_error is not None:
            raise first_error


class PageRealm:
    """Own the V8 context and binding session for one page."""

    def __init__(self, page, executor):
        self.page = page
        self.v8_context = None
        self.rejections = None
        self.executor = executor
        browser = page.profile.browser
        # Web objects built through this session receive PageServices, not the
        # platform Page.
        self.binding_session = BindingSession(browser.bindings, page.services)
        page.services.bind_realm(self)
        try:
            self.executor.call(self._initialize)
        except BaseException:
            # A stopped lane's invoke() may decline new work. Cleanup must still
            # run on the owner thread after a partially constructed V8 context.
            self.executor.group.executor.call(self._close_context)
            raise

    def _initialize(self):
        page = self.page
        browser = page.profile.browser
        binding_started = time.perf_counter_ns()
        self.binding_session.prepare("Window")
        binding_prepared = time.perf_counter_ns()
        #: A frame's realm is not the top page's: the label says which it is.
        context_type = "FRAME" if page.parent_page is not None else "TOP"
        self.agent = page.execution_group.agent(page.origin)
        page.services.task_services.prepare_animation_frames()
        self.v8_context = pyv8.V8Context(page.window, **_v8_context_kwargs(
            self.binding_session, context_type, page.trace,
            exposure_type="Window",
            on_materialize=self._record_deferred_materialized,
            trace_callback=page.profile.trace_callback,
            traceable=page.profile.traceable,
            isolate=page.execution_group.isolate,
            reuse_global=page.global_proxy,
        ), microtask_queue=self.agent.microtasks)
        #: Kept so the next document is handed the same window object.
        page.global_proxy = self.v8_context.glob
        self.executor.attach_deadline_source(self.v8_context)
        self.executor.bind_agent(self.agent, TaskContext(self, page.document, page.navigation.generation))
        context_created = time.perf_counter_ns()
        deferred_globals = _declare_realm_interfaces(
            self.v8_context,
            context_type="Window",
            bindings=browser.bindings,
        )
        deferred_declared_at = time.perf_counter_ns()
        # Chromium installs window.chrome before any page script runs.
        install_window_chrome(self.v8_context, page.services)
        _publish_window_name(self.v8_context, page)
        install_legacy_factories(
            self.v8_context, session=self.binding_session,
            table=browser.bindings.idl, document=page.document,
        )
        chrome_installed_at = time.perf_counter_ns()
        # The realm owns the context and the page's script controller borrows it,
        # so the isolate's error hooks and every Web API promise share one realm;
        # the hooks route exceptions V8 raises on its own.
        self.rejections = RejectionTracker(
            page.report_rejection,
            lambda callback: page.tasks.post(callback, TaskSource.DOM),
            WeakPromiseState(self.v8_context),
        )
        self.executor.attach_rejection_tracker(self.rejections)
        self.v8_context.set_error_hooks(
            page.report_uncaught_message, self.rejections.record,
        )
        plan = self.binding_session.bindings.plan("Window")
        browser._record_startup(
            binding_binder_ms=(binding_prepared - binding_started) / 1_000_000,
            profile_create_ms=(context_created - binding_prepared) / 1_000_000,
            deferred_global_declare_ms=(deferred_declared_at - context_created) / 1_000_000,
            chrome_install_ms=(chrome_installed_at - deferred_declared_at) / 1_000_000,
            core_interfaces=len(self.binding_session.materialized_interfaces),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            known_interfaces=len(plan.installs),
            deferred_globals=deferred_globals,
        )

    def _record_deferred_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        self.page.profile.browser._record_startup(
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            deferred_materialized=len(self.binding_session.materialized_interfaces)
            - len(self.binding_session.bindings.plan("Window").ready),
        )

    def enqueue_microtask(self, callback):
        return promises.enqueue_microtask(self.v8_context, callback)

    def make_promise(self):
        return promises.make_promise(self.v8_context)

    def promise_resolve(self, value):
        return promises.promise_resolve(self.v8_context, value)

    def close(self):
        """Close the document-scoped parts: the context and the binding session.

        ``self.v8_context`` going to ``None`` is what tells a late caller -- a promise of the
        replaced document, a microtask of the old realm -- that there is nothing to settle.
        """
        if self.executor.closed:
            return
        self.executor.call(self._close_context)

    def _close_context(self):
        if self.rejections is not None:
            self.rejections.close()
        v8_context = self.v8_context
        try:
            close = getattr(v8_context, "close", None)
            if callable(close):
                close()
        finally:
            self.executor.detach_deadline_source(v8_context)
            self.v8_context = None
            self.binding_session.detach()


__all__ = ["PageRealm", "WorkerRealm"]
