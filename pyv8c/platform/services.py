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
from typing import Any

from ..foundation.urls import origin_string
from .state import Ownership


@dataclass(frozen=True)
class WorkerContext:
    """Everything a worker thread needs from its parent page."""

    browser: Any
    session: Any
    universe: Any
    environment: Any
    trace: bool
    expose_unimplemented: bool
    stub_logging: str
    devtools: Any | None
    debug_parent: Any | None


class _TaskSource:
    """Task-queue access shared by the capabilities that post to the loop.

    The queue itself is held rather than reached through an owner, because a
    worker realm drives its own queue with the same capabilities a page does.
    """

    def __init__(self, tasks):
        self._tasks = tasks

    @property
    def closed(self):
        return self._tasks.closed

    def post_task(self, callback):
        self._tasks.post(callback)


class _RealmCapability(_TaskSource):
    """Realm identity, scripting and task posting (implements RealmServices)."""

    def __init__(self, owner):
        super().__init__(owner._tasks)
        self._owner = owner

    @property
    def url(self):
        return self._owner.window.location.href

    @property
    def environment(self):
        return self._owner.environment

    @property
    def origin(self):
        return self._owner._origin

    def make_promise(self):
        return self._owner._scripts.make_promise()

    def enqueue_microtask(self, callback):
        """Queue *callback* on the realm's V8 microtask queue.

        Promise reactions and ``queueMicrotask`` share that queue, which is what
        gives them the single FIFO order the HTML specification requires.
        """
        return self._owner._scripts.enqueue_microtask(callback)

    def evaluate_script(self, source):
        return self._owner._scripts.evaluate(source)

    def promise_resolve(self, value):
        return self._owner._scripts.promise_resolve(value)

    def js_error_value(self, exc):
        return self._owner._scripts.js_error_value(exc)

    def report_exception(self, exc):
        """Report an uncaught exception through this realm's error reporting.

        The DOM is what calls page code -- listeners, handler slots -- so an
        exception raised there has no caller left to catch it.  That is the case
        the specification calls "report the exception", and the task queue is
        the realm's reporting entry point: its sink turns it into the page's
        ``error`` event.
        """
        self._tasks.report_error(exc)

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
                except (KeyError, AttributeError):
                    # The property is not there, whichever way the binding says
                    # so: reading a JavaScript object by key raises KeyError, and
                    # only the attribute form raises AttributeError.
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



class _TaskCapability(_TaskSource):
    """Event-loop task sources (implements TaskServices)."""

    def schedule(self, callback, delay, repeat=False):
        return self._tasks.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self._tasks.cancel(ident)

    def begin_network(self):
        self._tasks.begin_network()

    def complete_network(self):
        self._tasks.complete_network()

    def begin_worker(self):
        self._tasks.begin_worker()

    def complete_worker(self):
        self._tasks.complete_worker()


class WorkerRealmServices:
    """The capability facets a worker hands to its own global scope.

    A worker realm owns its task queue and its V8 context, so ``setTimeout``
    and ``queueMicrotask`` behave exactly as they do on a page.  Everything the
    scope needs from its *parent* travels separately, through the
    ``WorkerServices`` facet the parent page established.
    """

    def __init__(self, tasks, runtime):
        self.task_services = _TaskCapability(tasks)
        self.realm_services = _WorkerRealmCapability(tasks, runtime)


class _WorkerRealmCapability(_TaskSource):
    """Worker scripting: the worker's own context, with no page behind it."""

    def __init__(self, tasks, runtime):
        super().__init__(tasks)
        self._runtime = runtime

    @property
    def url(self):
        return self._runtime.script_url

    def evaluate_script(self, source):
        return self._runtime.evaluate_script(source)

    def enqueue_microtask(self, callback):
        return self._runtime.enqueue_microtask(callback)

    def make_promise(self):
        return self._runtime.make_promise()

    def js_error_value(self, exc):
        return self._runtime.js_error_value(exc)

    def promise_resolve(self, value):
        return self._runtime.promise_resolve(value)

    def report_exception(self, exc):
        """Route a listener exception to the worker's error reporting.

        A worker global has no window to report onto: its reporting is the task
        queue's, which forwards the failure to the parent page as the worker's
        ``error`` event.
        """
        self._tasks.report_error(exc)


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

    def ensure_iframe_context(self, iframe):
        return self._owner._resources.ensure_iframe_context(iframe)

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

    def write_markup(self, markup):
        return self._owner._navigation.write_markup(markup)

    def node_adopted(self, node):
        if getattr(node, "localName", "") == "iframe":
            # An inserted frame gets a browsing context of its own, the way a
            # browser creates one for ``about:blank``.  Building a whole realm is
            # left to a queued task: an insertion happens while a script is
            # running, and entering another realm from there would re-enter this
            # one's executor.
            services = self._owner._resources
            services.ensure_iframe_context(node)
            if (node.get_src() and node.attributes.get("srcdoc") is None
                    and not getattr(node, "_resource_started", False)):
                services.discover_element_resource(node)
            return None
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
            trace=bool(self._owner.trace),
            expose_unimplemented=browser.expose_unimplemented,
            stub_logging=browser.stub_logging,
            devtools=self._owner._context._devtools,
            debug_parent=self._owner._page_realm.page._devtools_target,
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

    @property
    def http_available(self):
        backend = self._owner._context.network_backend
        return backend is not None and backend.http_available

    @property
    def ws_available(self):
        backend = self._owner._context.network_backend
        return backend is not None and backend.ws_available

    def websocket(self, url, protocols, callbacks):
        backend = self._owner._context.network_backend
        if backend is None:
            return None
        return backend.websocket(
            self._owner.task_services, url, protocols, callbacks, self._handshake(),
        )

    def _handshake(self):
        """What the page contributes to a WebSocket handshake."""
        environment = self._owner.environment
        options = {
            "headers": {
                "User-Agent": environment.user_agent,
                "Accept-Language": ", ".join(environment.languages),
            },
        }
        origin = origin_string(self._owner.realm_services.url)
        if origin != "null":
            options["origin"] = origin
        return options

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

    def __init__(self, context, environment, trace, ownership: Ownership,
                 navigation, scripts, resources, tasks, scope):
        self._context = context
        self.environment = environment
        self.trace = trace
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
        #: Granted ``sandbox`` tokens, or ``None`` when the realm is not sandboxed.
        self.sandbox_tokens = None

        # Narrow capabilities handed to web packages.
        self.realm_services = _RealmCapability(self)
        self.task_services = _TaskCapability(tasks)
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


__all__ = ["PageServices", "WorkerContext", "WorkerRealmServices"]
