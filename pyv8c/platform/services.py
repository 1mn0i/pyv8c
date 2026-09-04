"""Platform-owned service objects handed to web-facing interface code.

Web-layer objects never receive the full :class:`~pyv8c.platform.browser.Page`;
they get the narrow ``PageServices`` surface implemented here (the
``RealmServices``/``ResourceServices`` protocols from ``foundation.realm``),
plus the explicit ``WorkerContext`` bundle assembled for worker realms.
"""

from dataclasses import dataclass
from typing import Any, Optional

from .state import Ownership


@dataclass(frozen=True)
class WorkerContext:
    """Everything a worker thread needs from its parent page."""

    browser: Any
    session: Any
    universe: Any
    environment: Any
    hook: bool
    generate_stubs: bool
    expose_unimplemented: bool
    stub_logging: str
    devtools: Optional[Any]


class PageServices:
    """The full realm-service surface web-facing objects are allowed to use.

    Holds controllers, the owning context, and late-bound window/document
    references — never the Page itself, so web code cannot traverse into
    platform internals by construction.
    """

    def __init__(self, context, environment, hook, ownership: Ownership,
                 navigation, scripts, resources, tasks):
        self._context = context
        self.environment = environment
        self.hook = hook
        self._ownership = ownership
        self._navigation = navigation
        self._scripts = scripts
        self._resources = resources
        self._tasks = tasks
        # Late-bound by Page once window/document/realm exist.
        self.window = None
        self.document = None
        self._page_realm = None

    # -- realm identity -------------------------------------------------

    @property
    def url(self):
        return self.window.location.href

    @property
    def closed(self):
        return self._tasks.owner.closed

    # -- task scheduling --------------------------------------------------

    def post_task(self, callback):
        self._tasks.post(callback)

    def schedule(self, callback, delay, repeat=False):
        return self._tasks.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self._tasks.cancel(ident)

    @property
    def microtasks(self):
        return self._tasks.microtasks

    def begin_network(self):
        self._tasks.begin_network()

    def complete_network(self):
        self._tasks.complete_network()

    def begin_worker(self):
        self._tasks.begin_worker()

    def complete_worker(self):
        self._tasks.complete_worker()

    # -- scripting ----------------------------------------------------------

    def make_promise(self):
        return self._scripts.make_promise()

    def promise_resolve(self, value):
        return self._scripts.promise_resolve(value)

    def js_error_value(self, exc):
        return self._scripts.js_error_value(exc)

    def install_inline_handler(self, node, name, source):
        return self._scripts.install_inline_handler(node, name, source)

    def _node_adopted(self, node):
        return self._scripts.node_adopted(node)

    # -- navigation and parser hooks ----------------------------------------

    def element_class(self, tag):
        return self._navigation.element_class(tag)

    def request_navigation(self, url):
        return self._navigation.request_navigation(url)

    def _parser_script_start(self, node):
        return self._navigation.parser_script_start(node)

    def _parser_script_complete(self, node, source):
        return self._navigation.parser_script_complete(node, source)

    def _parser_is_blocked(self):
        return self._navigation.parser_is_blocked()

    @property
    def _parsing(self):
        return self._navigation.parsing

    # -- resources -----------------------------------------------------------

    def load_resource(self, url, request=None):
        return self._resources.load_resource(url, request)

    def load_resource_async(self, url, request, callback):
        holder = {}

        def complete(response, error):
            handle = holder.get("handle")
            try:
                callback(response, error)
            finally:
                if handle is not None:
                    self._untrack_resource(handle)

        handle = self._resources.load_resource_async(url, request, complete)
        holder["handle"] = handle
        self._track_resource(handle)
        return handle

    def load_iframe(self, iframe):
        return self._resources.load_iframe(iframe)

    def resolve_worker_source(self, script_url):
        return self._resources.resolve_worker_source(script_url)

    def _discover_element_resource(self, node):
        return self._resources.discover_element_resource(node)

    # -- context and browser services ----------------------------------------

    @property
    def cookie_jar(self):
        return self._context.cookies

    @property
    def context_services(self):
        return self._context

    @property
    def binding_services(self):
        return self._context.browser

    @property
    def devtools_server(self):
        return self._context._devtools

    def websocket(self, url, protocols, callbacks):
        backend = self._context.network_backend
        if backend is None:
            return None
        return backend.websocket(self, url, protocols, callbacks)

    def close_websocket(self, websocket):
        backend = self._context.network_backend
        if backend is not None:
            backend.close_websocket(websocket)

    @property
    def network_available(self):
        return self._context.network_backend is not None

    # -- workers --------------------------------------------------------------

    def worker_configuration(self):
        browser = self._context.browser
        session = browser.interface_universe.binding_session(self)
        return WorkerContext(
            browser=browser,
            session=session,
            universe=browser.interface_universe,
            environment=self.environment,
            hook=bool(self.hook),
            generate_stubs=browser.generate_interface_stubs,
            expose_unimplemented=browser.expose_unimplemented,
            stub_logging=browser.stub_logging,
            devtools=self.devtools_server,
        )

    def create_worker_realm(self, config):
        from .realms import WorkerRealm
        return WorkerRealm(self, config)

    # -- interface materialisation ----------------------------------------------

    def bind_realm(self, page_realm):
        self._page_realm = page_realm

    def materialize_interface(self, name):
        return self._page_realm.binding_session.materialize(name)

    def interface_materialized(self, name):
        return name in self._page_realm.binding_session.materialized_interfaces

    # -- ownership ---------------------------------------------------------------

    def _track_websocket(self, websocket):
        self._ownership.track("websockets", websocket)
        self._tasks.owner._resource_scope.add(lambda ws=websocket: self.close_websocket(ws))

    def _discard_websocket(self, websocket):
        self._ownership.discard("websockets", websocket)

    def _track_worker(self, worker):
        self._ownership.track("workers", worker)
        self._tasks.owner._resource_scope.add(worker.fn_terminate)

    def _discard_worker(self, worker):
        self._ownership.discard("workers", worker)

    def _track_frame(self, frame):
        self._ownership.track("frames", frame)
        self._tasks.owner._resource_scope.add(frame.close)

    def _track_resource(self, handle):
        if handle is not None:
            callback = handle.cancel
            if self._tasks.owner._resource_scope.add(callback):
                try:
                    handle._scope_callback = callback
                except AttributeError:
                    pass

    def _untrack_resource(self, handle):
        callback = getattr(handle, "_scope_callback", None)
        if callback is not None:
            self._tasks.owner._resource_scope.remove(callback)
            try:
                handle._scope_callback = None
            except AttributeError:
                pass


__all__ = ["PageServices", "WorkerContext"]
