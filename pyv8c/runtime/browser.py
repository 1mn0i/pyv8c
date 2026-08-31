"""Browser, browser context and Page orchestration facade.

The heavy lifting lives in small controllers owned by ``Page``:
``runtime/navigation.py``, ``runtime/lifecycle.py``, ``runtime/scripting.py``
and ``runtime/resource_loading.py``.  This module stays a thin composition
root: ``Page`` forwards its public API and delegates behaviour to those
controllers, so each concern can be read and tested on its own.
"""

import itertools
import os
import time
import uuid
from types import MappingProxyType
from urllib.parse import urlsplit

import pyv8

from ..bom.api import Window
from ..dom.model import Document
from ..network.backend import CronetNetworkBackend
from ..bindings.registry import expose_interfaces
from ..bindings.universe import (
    DEFAULT_EAGER_INTERFACES, BaselineRepository, InterfaceUniverse,
)
from ..core.errors import InvalidStateError, NetworkError, as_js_error
from .tasks import TaskQueue
from .state import StateAlias
from .navigation import NavigationController
from .lifecycle import DocumentLifecycle
from .scripting import ScriptController
from .resource_loading import ResourceController


class _ContextBootstrapProxy:
    """Placeholder published while native Context installs templates."""

    def __init__(self):
        self.args = ()
        self.kw = {}

    def expose(self, *_args, **_kwargs):
        return None

    def exec_js(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


class Page:
    _navigation_generation = StateAlias("navigation", "generation")
    _has_navigation = StateAlias("navigation", "has_navigation")
    _pending_navigation = StateAlias("navigation", "pending")
    _parser = StateAlias("navigation", "parser")
    _parser_generation = StateAlias("navigation", "parser_generation")
    _parsing = StateAlias("navigation", "parsing")
    _parser_done = StateAlias("navigation", "parser_done")
    _dom_content_loaded = StateAlias("lifecycle", "dom_content_loaded")
    _document_load_fired = StateAlias("lifecycle", "document_load_fired")
    _load_waiters = StateAlias("lifecycle", "load_waiters")
    _dom_blocking_scripts = StateAlias("scripts", "dom_blocking_scripts")
    _script_queue = StateAlias("scripts", "queue")
    _defer_scripts = StateAlias("scripts", "defer")
    _module_cache = StateAlias("scripts", "module_cache")
    _document_resources = StateAlias("resources", "resources")
    _resource_counter = StateAlias("resources", "counter")

    def __init__(self, context, url="about:blank"):
        self.context_owner = context
        self.closed = False
        self.navigation = NavigationController(self)
        self.lifecycle = DocumentLifecycle(self)
        self.scripts = ScriptController(self)
        self.resources = ResourceController(self)
        self.tasks = TaskQueue(self, self._run_queued_callback)
        self.microtasks = self.tasks.microtasks
        self._task_errors = self.tasks.errors
        self._promise_counter = itertools.count(1)
        self._owned_websockets = set()
        self._owned_workers = set()
        self._owned_frames = set()
        self.document = Document(page=self, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.network_backend = context.network_backend
        self.window = Window(page=self)
        self.binding_session = context.browser.interface_universe.binding_session(self)
        self.window.location.href = str(url)
        # Native template installation can inspect the global object before
        # Context.__init__ returns.  Publish a sentinel so Window callbacks
        # never observe a partially initialized Page attribute.
        self.context = _ContextBootstrapProxy()
        binding_started = time.perf_counter_ns()
        eager_configs = self.binding_session.class_configs(
            "Window", roots=self.binding_session.universe.eager_names("Window"),
        )
        binding_prepared = time.perf_counter_ns()
        context_kwargs = {
            "hook": False,
            "ctx_type": "Window",
            "class_configs": self.binding_session.configs,
            "lazy_factory": self.binding_session.lazy_factory(
                "Window", on_materialize=self._record_lazy_materialized,
            ),
        }
        self.context = pyv8.Context(self.window, **context_kwargs)
        context_created = time.perf_counter_ns()
        expose_interfaces(
            self.context,
            context_type="Window",
            generate_stubs=self.context_owner.browser.generate_interface_stubs,
            expose_unimplemented=self.context_owner.browser.expose_unimplemented,
            stub_logging=self.context_owner.browser.stub_logging,
            universe=self.context_owner.browser.interface_universe,
            lazy_interfaces=True,
            class_for=self.binding_session.class_for,
        )
        # Declare every remaining exposed name on the global object. Native
        # accessors materialise the interface on first property access, so this
        # costs no template construction and creates no Python stubs.
        lazy_names = self.binding_session.lazy_names("Window")
        lazy_registered = self.context.declare_lazy(
            lazy_names, skip=self.binding_session.universe.eager_names("Window"),
        )
        lazy_registered_at = time.perf_counter_ns()
        self.context_owner.browser._record_startup(
            binding_plan_ms=(binding_prepared - binding_started) / 1_000_000,
            context_create_ms=(context_created - binding_prepared) / 1_000_000,
            lazy_register_ms=(lazy_registered_at - context_created) / 1_000_000,
            eager_interfaces=len(self.binding_session.materialized_interfaces),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            interface_names=len(self.binding_session.interface_names("Window")),
            lazy_registered=lazy_registered,
        )

    @property
    def url(self):
        return self.window.location.href

    @url.setter
    def url(self, value):
        self.goto(value)

    def _record_lazy_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        self.context_owner.browser._record_startup(
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            lazy_materialized=len(self.binding_session.materialized_interfaces)
            - len(self.binding_session.universe.eager_names("Window")),
        )

    # -- navigation facade ---------------------------------------------

    def set_content(self, html, url="about:blank"):
        return self.navigation.set_content(html, url)

    def goto(self, url, *, timeout=30, wait_until="load"):
        return self.navigation.goto(url, timeout=timeout, wait_until=wait_until)

    def request_navigation(self, url):
        return self.navigation.request_navigation(url)

    def element_class(self, tag):
        return self.navigation.element_class(tag)

    def _parser_script_start(self, node):
        return self.navigation.parser_script_start(node)

    def _parser_script_complete(self, node, source):
        return self.navigation.parser_script_complete(node, source)

    def _parser_is_blocked(self):
        return self.navigation.parser_is_blocked()

    # -- scripting facade ----------------------------------------------

    def evaluate(self, script, filename=None):
        return self.scripts.evaluate(script, filename=filename)

    def install_inline_handler(self, node, name, source):
        return self.scripts.install_inline_handler(node, name, source)

    def _node_adopted(self, node):
        return self.scripts.node_adopted(node)

    def make_promise(self):
        return self.scripts.make_promise()

    def js_error_value(self, exc):
        return self.scripts.js_error_value(exc)

    def promise_resolve(self, value):
        return self.scripts.promise_resolve(value)

    # -- resource facade ------------------------------------------------

    def load_resource_async(self, url, request, callback):
        return self.resources.load_resource_async(url, request, callback)

    def _discover_element_resource(self, node):
        return self.resources.discover_element_resource(node)

    def load_iframe(self, iframe):
        return self.resources.load_iframe(iframe)

    def resolve_worker_source(self, script_url):
        return self.resources.resolve_worker_source(script_url)

    # -- task facade ----------------------------------------------------

    def post_task(self, callback):
        self.tasks.post(callback)

    def schedule(self, callback, delay, repeat=False):
        return self.tasks.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self.tasks.cancel(ident)

    def run_until_idle(self, timeout=0):
        self.tasks.run_until_idle(timeout)

    def wait_for_network(self, timeout=30):
        self.run_until_idle(timeout=timeout)

    def begin_network(self):
        self.tasks.begin_network()

    def complete_network(self):
        self.tasks.complete_network()

    def begin_worker(self):
        self.tasks.begin_worker()

    def complete_worker(self):
        self.tasks.complete_worker()

    def _run_callback(self, callback, *args):
        return callback(*args)

    def _run_queued_callback(self, callback, *args):
        """Keep one faulty task from aborting unrelated browser work."""
        try:
            return self._run_callback(callback, *args)
        except BaseException as exc:
            self._task_errors.append(as_js_error(exc))
            return None

    def close(self):
        if self.closed:
            return
        if self._has_navigation:
            self.lifecycle.dispatch_beforeunload()
            self.lifecycle.dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self.lifecycle.dispatch_page_event("unload", self.window, {"bubbles": False})
        # Drop cached JS scripts before the V8 context goes away.
        self.scripts.clear_js_helpers()
        self.closed = True
        self.tasks.close()
        self.resources.reset()
        self.lifecycle.load_waiters.clear()
        for frame in list(self._owned_frames):
            frame.close()
        self._owned_frames.clear()
        for ws in list(self._owned_websockets):
            if self.network_backend:
                self.network_backend.close_websocket(ws)
        self._owned_websockets.clear()
        for worker in list(self._owned_workers):
            worker.fn_terminate()
        self._owned_workers.clear()
        self.context_owner._discard_page(self)
        close_context = getattr(self.context, "close", None)
        if callable(close_context):
            close_context()


class BrowserContext:
    def __init__(self, browser):
        self.browser, self.pages = browser, []
        self.network_backend = CronetNetworkBackend(options=browser.cycronet_options) if browser.real_network else None
        self._blob_urls = {}
        self.closed = False

    def _ensure_open(self):
        if self.closed or self.browser._closed:
            raise InvalidStateError("BrowserContext is closed")

    @staticmethod
    def _serialized_origin(url):
        """Return the browser serialized origin used in a Blob URL."""
        try:
            parts = urlsplit(str(url))
            if parts.scheme not in ("http", "https", "ws", "wss", "ftp") or not parts.hostname:
                return "null"
            host = parts.hostname.lower()
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            port = parts.port
            default_port = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}.get(parts.scheme)
            suffix = (":" + str(port)) if port is not None and port != default_port else ""
            return f"{parts.scheme.lower()}://{host}{suffix}"
        except (TypeError, ValueError):
            return "null"

    def create_blob_url(self, blob, page=None):
        self._ensure_open()
        origin = self._serialized_origin(getattr(page, "url", "about:blank"))
        url = "blob:" + origin + "/" + str(uuid.uuid4())
        self._blob_urls[url] = (bytes(blob._data), blob.get_type())
        return url

    def resolve_blob_url(self, url):
        self._ensure_open()
        if url not in self._blob_urls:
            raise NetworkError("revoked or unknown Blob URL")
        return self._blob_urls[url]

    def revoke_blob_url(self, url):
        self._ensure_open()
        self._blob_urls.pop(url, None)

    def new_page(self, url="about:blank"):
        self._ensure_open()
        page = Page(self)
        self.pages.append(page)
        if str(url) != "about:blank":
            try:
                page.goto(url)
            except BaseException:
                page.close()
                raise
        return page

    def _discard_page(self, page):
        try:
            self.pages.remove(page)
        except ValueError:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        for page in list(self.pages):
            page.close()
        self.pages.clear()
        self._blob_urls.clear()
        if self.network_backend:
            self.network_backend.close()
            self.network_backend = None


class Browser:
    def __init__(self, *, resource_loader=None,
                 real_network=False, cycronet_options=None,
                 generate_interface_stubs=True, expose_unimplemented=True,
                 stub_logging="info", interface_baseline_path=None,
                 eager_interfaces=None, strict_validation=None):
        started = time.perf_counter_ns()
        self._closed = False
        # pyv8 initializes its process-global V8 runtime during package import.
        self.contexts = []
        self.resource_loader = resource_loader
        self.real_network = bool(real_network)
        self.cycronet_options = dict(cycronet_options or {})
        self.generate_interface_stubs = bool(generate_interface_stubs)
        self.expose_unimplemented = bool(expose_unimplemented)
        self.stub_logging = str(stub_logging or "info")
        self._startup_stats = {}
        strict = os.environ.get("PYV8C_STRICT_VALIDATION") == "1" if strict_validation is None else bool(strict_validation)
        snapshot = BaselineRepository.load(interface_baseline_path)
        loaded = time.perf_counter_ns()
        self.interface_universe = InterfaceUniverse(
            snapshot, stub_logging=self.stub_logging,
            expose_unimplemented=self.expose_unimplemented,
            eager_interfaces=DEFAULT_EAGER_INTERFACES if eager_interfaces is None else eager_interfaces,
            strict_validation=strict,
            lazy_interfaces=True,
        )
        if strict:
            validation_started = time.perf_counter_ns()
            self.interface_universe.validate()
            validation_finished = time.perf_counter_ns()
        else:
            validation_finished = validation_started = loaded
        self.interface_baseline_path = snapshot.path
        self.interface_baseline_info = snapshot.info
        self._startup_stats.update(BaselineRepository._last_timings)
        self._startup_stats.update({
            "validation_ms": (validation_finished - validation_started) / 1_000_000,
            "browser_init_ms": (validation_finished - started) / 1_000_000,
            "interface_names": len(self.interface_universe.interface_names("Window")),
            "eager_interfaces": len(self.interface_universe.eager_names("Window")),
            "lazy_interfaces": max(0, len(self.interface_universe.interface_names("Window")) - len(self.interface_universe.eager_names("Window"))),
            "materialized_interfaces": 0,
            "binding_plan_ms": 0.0,
            "context_create_ms": 0.0,
            "lazy_register_ms": 0.0,
            "lazy_registered": 0,
            "lazy_materialized": 0,
        })

    @property
    def startup_stats(self):
        return MappingProxyType(dict(self._startup_stats))

    def _record_startup(self, **values):
        if os.environ.get("PYV8C_STARTUP_STATS", "1") != "0":
            self._startup_stats.update(values)

    def new_context(self):
        if self._closed:
            raise InvalidStateError("Browser is closed")
        context = BrowserContext(self)
        self.contexts.append(context)
        return context

    def close(self):
        if self._closed:
            return
        for context in self.contexts:
            context.close()
        self.contexts.clear()
        self._closed = True
