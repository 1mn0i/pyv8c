"""Platform-owned service objects handed to web-facing interface code.

``PageServices`` is the composition root for one page realm.  It owns the
controllers (navigation, scripts, resources, tasks) and builds the six narrow
capability objects from ``foundation.realm`` -- ``realm_services``,
``task_services``, ``resource_services``, ``document_services``,
``worker_services`` and ``context_services``.  Web packages (``web/dom``,
``web/fetch``, ``web/network``, ``web/bom``, ``web/workers``) reach platform
functionality only through those capability facets, never through the Page.

``PageServices`` itself also implements the six protocols so the platform
controllers and the binding session can keep using the wide surface; the
structurally enforced boundary is the one web code crosses: no web module
touches a Page attribute, a network backend or the interface universe.
"""

from dataclasses import dataclass
from typing import Any, Optional

from ..foundation.realm import (
    ContextServices,
    DocumentServices,
    RealmServices,
    ResourceServices,
    TaskServices,
    WorkerServices,
)
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


class _RealmCapability:
    """Realm identity, scripting and task posting (implements RealmServices)."""

    def __init__(self, owner):
        self._owner = owner

    @property
    def url(self):
        return self._owner.window.location.href

    @property
    def closed(self):
        return self._owner._tasks.closed

    @property
    def environment(self):
        return self._owner.environment

    def post_task(self, callback):
        self._owner._tasks.post(callback)

    def make_promise(self):
        return self._owner._scripts.make_promise()

    def promise_resolve(self, value):
        return self._owner._scripts.promise_resolve(value)

    def js_error_value(self, exc):
        return self._owner._scripts.js_error_value(exc)

    def materialize_interface(self, name):
        return self._owner.materialize_interface(name)

    def interface_materialized(self, name):
        return self._owner.interface_materialized(name)


class _TaskCapability:
    """Event-loop task sources (implements TaskServices)."""

    def __init__(self, owner):
        self._owner = owner

    def post_task(self, callback):
        self._owner._tasks.post(callback)

    def schedule(self, callback, delay, repeat=False):
        return self._owner._tasks.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self._owner._tasks.cancel(ident)

    @property
    def microtasks(self):
        return self._owner._tasks.microtasks

    def begin_network(self):
        self._owner._tasks.begin_network()

    def complete_network(self):
        self._owner._tasks.complete_network()

    def begin_worker(self):
        self._owner._tasks.begin_worker()

    def complete_worker(self):
        self._owner._tasks.complete_worker()


class _ResourceCapability:
    """Resource loading and ownership (implements ResourceServices)."""

    def __init__(self, owner):
        self._owner = owner

    def load_resource(self, url, request=None):
        return self._owner._resources.load_resource(url, request)

    def load_resource_async(self, url, request, callback):
        holder = {}

        def complete(response, error):
            handle = holder.get("handle")
            try:
                callback(response, error)
            finally:
                if handle is not None:
                    self._untrack_resource(handle)

        handle = self._owner._resources.load_resource_async(url, request, complete)
        holder["handle"] = handle
        self._track_resource(handle)
        return handle

    def load_iframe(self, iframe):
        return self._owner._resources.load_iframe(iframe)

    def resolve_worker_source(self, script_url):
        return self._owner._resources.resolve_worker_source(script_url)

    def discover_element_resource(self, node):
        return self._owner._resources.discover_element_resource(node)

    def _track_resource(self, handle):
        if handle is not None:
            callback = handle.cancel
            if self._owner._scope.add(callback):
                try:
                    handle._scope_callback = callback
                except AttributeError:
                    pass

    def _untrack_resource(self, handle):
        callback = getattr(handle, "_scope_callback", None)
        if callback is not None:
            self._owner._scope.remove(callback)
            try:
                handle._scope_callback = None
            except AttributeError:
                pass


class _DocumentCapability:
    """Document, window and parser hooks (implements DocumentServices)."""

    def __init__(self, owner):
        self._owner = owner

    @property
    def window(self):
        return self._owner.window

    @property
    def document(self):
        return self._owner.document

    def element_class(self, tag):
        return self._owner._navigation.element_class(tag)

    def install_inline_handler(self, node, name, source):
        return self._owner._scripts.install_inline_handler(node, name, source)

    def parser_script_start(self, node):
        return self._owner._navigation.parser_script_start(node)

    def parser_script_complete(self, node, source):
        return self._owner._navigation.parser_script_complete(node, source)

    def parser_is_blocked(self):
        return self._owner._navigation.parser_is_blocked()

    def request_navigation(self, url):
        return self._owner._navigation.request_navigation(url)

    @property
    def parsing(self):
        return self._owner._navigation.parsing


class _WorkerCapability:
    """Worker spawning and tracking (implements WorkerServices)."""

    def __init__(self, owner):
        self._owner = owner

    def worker_configuration(self):
        browser = self._owner._context.browser
        session = browser.interface_universe.binding_session(self._owner)
        return WorkerContext(
            browser=browser,
            session=session,
            universe=browser.interface_universe,
            environment=self._owner.environment,
            hook=bool(self._owner.hook),
            generate_stubs=browser.generate_interface_stubs,
            expose_unimplemented=browser.expose_unimplemented,
            stub_logging=browser.stub_logging,
            devtools=self._owner.devtools_server,
        )

    def create_worker_realm(self, config):
        from .realms import WorkerRealm
        return WorkerRealm(self._owner, config)

    def track_worker(self, worker):
        self._owner._ownership.track("workers", worker)
        self._owner._scope.add(worker.fn_terminate)

    def discard_worker(self, worker):
        self._owner._ownership.discard("workers", worker)


class _ContextCapability:
    """Browser-context services: blobs, cookies, sockets (implements ContextServices)."""

    def __init__(self, owner):
        self._owner = owner

    def create_blob_url(self, blob, realm=None, page=None):
        # ``page=`` is the historical spelling used by web/bom; the protocol
        # documents ``realm=``.  Both refer to the page whose URL becomes the
        # blob's origin.
        return self._owner._context.create_blob_url(blob, page=page if page is not None else realm)

    def revoke_blob_url(self, url):
        self._owner._context.revoke_blob_url(url)

    def resolve_blob_url(self, url):
        return self._owner._context.resolve_blob_url(url)

    @property
    def cookie_jar(self):
        return self._owner._context.cookies

    @property
    def network_available(self):
        return self._owner._context.network_backend is not None

    def websocket(self, url, protocols, callbacks):
        backend = self._owner._context.network_backend
        if backend is None:
            return None
        return backend.websocket(self._owner, url, protocols, callbacks)

    def close_websocket(self, websocket):
        backend = self._owner._context.network_backend
        if backend is not None:
            backend.close_websocket(websocket)

    def track_websocket(self, websocket):
        self._owner._ownership.track("websockets", websocket)
        self._owner._scope.add(lambda ws=websocket: self.close_websocket(ws))

    def discard_websocket(self, websocket):
        self._owner._ownership.discard("websockets", websocket)

    def track_frame(self, frame):
        self._owner._ownership.track("frames", frame)
        self._owner._scope.add(frame.close)


class PageServices:
    """Composition root assembling the six realm capabilities.

    Holds controllers, the owning context, and late-bound window/document
    references — never the Page itself, so web code cannot traverse into
    platform internals by construction.  Web packages consume the narrow
    capability facets below (``realm_services`` .. ``context_services``);
    the protocol method spellings that follow exist so platform controllers
    and the binding session can keep a single service object.  They are thin
    delegates to the facets and must not grow new logic.
    """

    def __init__(self, context, environment, hook, ownership: Ownership,
                 navigation, scripts, resources, tasks, scope):
        self._context = context
        self.environment = environment
        self.hook = hook
        self._ownership = ownership
        self._navigation = navigation
        self._scripts = scripts
        self._resources = resources
        self._tasks = tasks
        # Owned directly instead of reached through ``tasks.owner``: the task
        # queue lives in foundation and must not be a back door to the Page.
        self._scope = scope
        # Late-bound by Page once window/document/realm exist.
        self.window = None
        self.document = None
        self._page_realm = None

        # Narrow capabilities handed to web packages.
        self.realm_services = _RealmCapability(self)
        self.task_services = _TaskCapability(self)
        self.resource_services = _ResourceCapability(self)
        self.document_services = _DocumentCapability(self)
        self.worker_services = _WorkerCapability(self)
        self.context_services = _ContextCapability(self)

    # -- realm identity -------------------------------------------------

    @property
    def url(self):
        return self.window.location.href

    @property
    def closed(self):
        return self._tasks.closed

    # -- task scheduling --------------------------------------------------

    def post_task(self, callback):
        self.task_services.post_task(callback)

    def schedule(self, callback, delay, repeat=False):
        return self.task_services.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self.task_services.cancel(ident)

    @property
    def microtasks(self):
        return self.task_services.microtasks

    def begin_network(self):
        self.task_services.begin_network()

    def complete_network(self):
        self.task_services.complete_network()

    def begin_worker(self):
        self.task_services.begin_worker()

    def complete_worker(self):
        self.task_services.complete_worker()

    # -- scripting ----------------------------------------------------------

    def make_promise(self):
        return self.realm_services.make_promise()

    def promise_resolve(self, value):
        return self.realm_services.promise_resolve(value)

    def js_error_value(self, exc):
        return self.realm_services.js_error_value(exc)

    def install_inline_handler(self, node, name, source):
        return self.document_services.install_inline_handler(node, name, source)

    def _node_adopted(self, node):
        return self._scripts.node_adopted(node)

    # -- navigation and parser hooks ----------------------------------------

    def element_class(self, tag):
        return self.document_services.element_class(tag)

    def request_navigation(self, url):
        return self.document_services.request_navigation(url)

    @property
    def _parsing(self):
        # Historical spelling still consulted by web/dom/model through
        # ``getattr``; the protocol property is ``document_services.parsing``.
        return self.document_services.parsing

    # Public protocol spellings (DocumentServices / WorkerServices).

    def parser_script_start(self, node):
        return self.document_services.parser_script_start(node)

    def parser_script_complete(self, node, source):
        return self.document_services.parser_script_complete(node, source)

    def parser_is_blocked(self):
        return self.document_services.parser_is_blocked()

    def discover_element_resource(self, node):
        return self.resource_services.discover_element_resource(node)

    # -- resources -----------------------------------------------------------

    def load_resource(self, url, request=None):
        return self.resource_services.load_resource(url, request)

    def load_resource_async(self, url, request, callback):
        return self.resource_services.load_resource_async(url, request, callback)

    def load_iframe(self, iframe):
        return self.resource_services.load_iframe(iframe)

    def resolve_worker_source(self, script_url):
        return self.resource_services.resolve_worker_source(script_url)

    # -- context and browser services ----------------------------------------

    @property
    def cookie_jar(self):
        return self.context_services.cookie_jar

    @property
    def binding_services(self):
        return self._context.browser

    @property
    def devtools_server(self):
        return self._context._devtools

    def websocket(self, url, protocols, callbacks):
        return self.context_services.websocket(url, protocols, callbacks)

    def close_websocket(self, websocket):
        self.context_services.close_websocket(websocket)

    @property
    def network_available(self):
        return self.context_services.network_available

    # -- workers --------------------------------------------------------------

    def worker_configuration(self):
        return self.worker_services.worker_configuration()

    def create_worker_realm(self, config):
        return self.worker_services.create_worker_realm(config)

    def track_worker(self, worker):
        self.worker_services.track_worker(worker)

    def discard_worker(self, worker):
        self.worker_services.discard_worker(worker)

    # -- interface materialisation ----------------------------------------------

    def bind_realm(self, page_realm):
        self._page_realm = page_realm

    def materialize_interface(self, name):
        return self._page_realm.binding_session.materialize(name)

    def interface_materialized(self, name):
        return name in self._page_realm.binding_session.materialized_interfaces

    # -- ownership ---------------------------------------------------------------

    def _track_frame(self, frame):
        self.context_services.track_frame(frame)

    def _track_resource(self, handle):
        self.resource_services._track_resource(handle)

    def _untrack_resource(self, handle):
        self.resource_services._untrack_resource(handle)


__all__ = ["PageServices", "WorkerContext"]
