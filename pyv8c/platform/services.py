"""Platform-owned service objects handed to web-facing interface code.

``PageServices`` is the composition root for one page realm.  It owns the
controllers (navigation, scripts, resources, tasks) and builds the six narrow
capability objects from ``foundation.realm`` -- ``realm_services``,
``task_services``, ``resource_services``, ``document_services``,
``worker_services`` and ``context_services``.  Web packages (``web/dom``,
``web/fetch``, ``web/network``, ``web/bom``, ``web/workers``) reach platform
functionality only through those capability facets, never through the Page.

``PageServices`` is only an assembly object. Protocol implementations live on
the individual capability objects, which prevents a second, wide forwarding
surface from drifting away from the contracts web code actually consumes.
"""

import threading
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

    @property
    def origin(self):
        return self._owner._origin

    def post_task(self, callback):
        self._owner._tasks.post(callback)

    def make_promise(self):
        return self._owner._scripts.make_promise()

    def promise_resolve(self, value):
        return self._owner._scripts.promise_resolve(value)

    def js_error_value(self, exc):
        return self._owner._scripts.js_error_value(exc)

    def materialize_interface(self, name):
        return self._owner._page_realm.binding_session.materialize(name)

    def interface_materialized(self, name):
        return name in self._owner._page_realm.binding_session.materialized_interfaces

    def window_property(self, operation, name, value=None):
        """Operate on a same-origin Window in its owning realm."""
        realm = self._owner._page_realm
        def apply():
            target = realm.context.js_obj(self._owner.window)
            if operation == "get":
                try:
                    return target[name]
                except AttributeError:
                    import pyv8
                    return pyv8.Undefined
            if operation == "set":
                target[name] = value
            elif operation == "delete":
                del target[name]
            elif operation == "query":
                return 0 if name in dir(target) else None
        # Cross-window property access is synchronous and can originate in a
        # V8 callback that already holds the shared isolate lock.  Posting to
        # another executor would wait on that same lock and deadlock.  The
        # native JSObject operation enters its saved Context reentrantly.
        return apply()



class _TaskCapability:
    """Event-loop task sources (implements TaskServices)."""

    def __init__(self, owner):
        self._owner = owner

    @property
    def closed(self):
        return self._owner._tasks.closed

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
        # A request can finish before this call returns: the network thread
        # pool invokes ``complete`` independently of whoever issued the
        # request.  Registering afterwards therefore lost the race -- the
        # delivery untracked a handle that had no registration yet, and the
        # cancel callback stayed behind until the page closed.  The state
        # machine below makes the two orders converge on one untrack.
        state = {"handle": None, "tracked": False, "done": False}
        lock = threading.Lock()

        def complete(response, error):
            with lock:
                state["done"] = True
                handle, tracked = state["handle"], state["tracked"]
            try:
                callback(response, error)
            finally:
                if handle is not None and tracked:
                    self._untrack_resource(handle)

        handle = self._owner._resources.load_resource_async(url, request, complete)
        with lock:
            state["handle"] = handle
            if handle is not None:
                self._track_resource(handle)
                state["tracked"] = True
            completed_early = state["done"]
        if completed_early and handle is not None:
            self._untrack_resource(handle)
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
            self._owner._register_owned("resources", handle, callback)
            try:
                handle._scope_callback = callback
            except AttributeError:
                pass

    def _untrack_resource(self, handle):
        callback = getattr(handle, "_scope_callback", None)
        if callback is not None:
            self._owner._unregister_owned("resources", handle)
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

    def node_adopted(self, node):
        return self._owner._scripts.node_adopted(node)

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
            devtools=self._owner._context._devtools,
        )

    def create_worker_realm(self, config):
        from .realms import WorkerRealm
        return WorkerRealm(self._owner, config)

    def track_worker(self, worker):
        self._owner._ownership.track("workers", worker)
        self._owner._register_owned("workers", worker, worker.fn_terminate)

    def discard_worker(self, worker):
        self._owner._ownership.discard("workers", worker)
        self._owner._unregister_owned("workers", worker)


class _ContextCapability:
    """Browser-context services: blobs, cookies, sockets (implements ContextServices)."""

    def __init__(self, owner):
        self._owner = owner

    def create_blob_url(self, blob, realm=None):
        return self._owner._context.create_blob_url(blob, realm=realm)

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
        return backend.websocket(self._owner.task_services, url, protocols, callbacks)

    def close_websocket(self, websocket):
        backend = self._owner._context.network_backend
        if backend is not None:
            backend.close_websocket(websocket)

    def track_websocket(self, websocket):
        self._owner._ownership.track("websockets", websocket)
        self._owner._register_owned(
            "websockets", websocket,
            lambda ws=websocket: self.close_websocket(ws),
        )

    def discard_websocket(self, websocket):
        self._owner._ownership.discard("websockets", websocket)
        self._owner._unregister_owned("websockets", websocket)

    def discard_frame(self, frame):
        self._owner._ownership.discard("frames", frame)
        self._owner._unregister_owned("frames", frame)

    def track_frame(self, frame):
        self._owner._ownership.track("frames", frame)
        self._owner._register_owned("frames", frame, frame.close)
        try:
            frame._parent_scope_cleanup = lambda child=frame: self.discard_frame(child)
        except AttributeError:
            pass


class PageServices:
    """Composition root assembling the six realm capabilities.

    Holds controllers, the owning context, and late-bound window/document
    references — never the Page itself, so web code cannot traverse into
    platform internals by construction. Web packages consume the narrow
    capability facets below (``realm_services`` .. ``context_services``).
    """

    def __init__(self, context, environment, hook, ownership: Ownership,
                 navigation, scripts, resources, tasks, scope):
        self._context = context
        self.environment = environment
        self.hook = hook
        self._ownership = ownership
        # Keep the exact callback object so resources can unregister from the
        # scope when they complete normally instead of accumulating closures.
        self._registrations = {}
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
        self._origin = ("opaque", object())

        # Narrow capabilities handed to web packages.
        self.realm_services = _RealmCapability(self)
        self.task_services = _TaskCapability(self)
        self.resource_services = _ResourceCapability(self)
        self.document_services = _DocumentCapability(self)
        self.worker_services = _WorkerCapability(self)
        self.context_services = _ContextCapability(self)

    def _register_owned(self, kind, resource, callback):
        key = (kind, id(resource))
        previous = self._registrations.get(key)
        if previous is not None:
            return previous[1]
        if self._scope.add(callback):
            self._registrations[key] = (resource, callback)
        return callback

    def _unregister_owned(self, kind, resource):
        key = (kind, id(resource))
        registration = self._registrations.pop(key, None)
        if registration is not None:
            self._scope.remove(registration[1])

    def bind_realm(self, page_realm):
        self._page_realm = page_realm


__all__ = ["PageServices", "WorkerContext"]
