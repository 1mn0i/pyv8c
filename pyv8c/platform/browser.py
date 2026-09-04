"""Browser, browser context and Page orchestration facade.

The heavy lifting lives in small controllers owned by ``Page``:
``platform/navigation.py``, ``platform/lifecycle.py``, ``platform/scripting.py``
and ``platform/resource_loading.py``.  This module stays a thin composition
root: ``Page`` forwards its public API and delegates behaviour to those
controllers, so each concern can be read and tested on its own.
"""

import os
import time
import threading
import uuid
from types import MappingProxyType
from urllib.parse import urlsplit

# Kept as a module attribute for diagnostics and existing Context-construction
# instrumentation; PageRealm uses the same native module object.
import pyv8

from ..web.bom.api import Window
from ..web.dom.model import Document
from ..web.network.backend import CronetNetworkBackend
from ..web.network.cookies import CookieJar
from ..engine.universe import (
    DEFAULT_EAGER_INTERFACES, BaselineRepository, InterfaceUniverse,
)
from ..foundation.errors import InvalidStateError, NetworkError
from ..foundation.environment import BrowserEnvironment
from ..foundation.tasks import TaskQueue
from ..foundation.lifecycle import LifecycleState, ResourceScope
from .state import StateAlias, Ownership
from .services import PageServices
from .bootstrap import bootstrap_interfaces
from .navigation import NavigationController
from .lifecycle import DocumentLifecycle
from .scripting import ScriptController
from .resource_loading import ResourceController
from .realms import PageRealm


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
    # Only the alias actually consumed outside the controllers is kept:
    # close() reads _has_navigation.
    _has_navigation = StateAlias("navigation", "has_navigation")

    def __init__(self, context, url="about:blank", *, hook=None, environment=None):
        self.context_owner = context
        self.environment = context.browser.environment.overlay(environment)
        # A page can override the browser default without changing the
        # configuration used by other pages in the same browser.
        self.hook = context.browser.hook if hook is None else bool(hook)
        self.closed = False
        self._resource_scope = ResourceScope()
        self._close_lock = threading.Lock()
        self._closing = False
        self.navigation = NavigationController(self)
        self.lifecycle = DocumentLifecycle(self)
        self.scripts = ScriptController(self)
        self.resources = ResourceController(self)
        self.tasks = TaskQueue(self)
        self._owned = Ownership()
        # Web-facing objects never see this Page; they receive the narrow
        # PageServices surface assembled from the controllers above.
        self.services = PageServices(
            context, self.environment, self.hook, self._owned,
            self.navigation, self.scripts, self.resources, self.tasks,
        )
        self.document = Document(services=self.services, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.network_backend = context.network_backend
        self.window = Window(services=self.services, environment=self.environment)
        self.services.window = self.window
        self.services.document = self.document
        self.window.location.href = str(url)
        # Native template installation can inspect the global object before
        # Context.__init__ returns.  Publish a sentinel so Window callbacks
        # never observe a partially initialized Page attribute.
        self.context = _ContextBootstrapProxy()
        self._devtools_target = None
        self.realm = PageRealm(self)
        self.services.bind_realm(self.realm)
        self.realm.executor.attach_task_queue(self.tasks)
        self.context = self.realm.context

    @property
    def binding_session(self):
        return self.realm.binding_session

    @property
    def url(self):
        return self.window.location.href

    @url.setter
    def url(self, value):
        self.goto(value)

    # -- public API ------------------------------------------------------
    # Everything else web-facing code needs is served by ``self.services``
    # (PageServices); controllers reach each other through the Page attributes
    # directly.  Only user-facing forwards remain here.

    def set_content(self, html, url="about:blank"):
        return self.realm.executor.call(self.navigation.set_content, html, url)

    def goto(self, url, *, timeout=30, wait_until="load"):
        return self.realm.executor.call(
            self.navigation.goto, url, timeout=timeout, wait_until=wait_until,
        )

    def evaluate(self, script, filename=None):
        return self.realm.executor.call(
            self.scripts.evaluate, script, filename=filename,
        )

    def run_until_idle(self, timeout=0):
        self.realm.executor.call(self.tasks.run_until_idle, timeout)

    def close(self):
        callbacks = self._resource_scope.begin_close()
        if callbacks is None:
            return
        with self._close_lock:
            if self.closed or self._closing:
                return
            self._closing = True
        # A paused inspector loop blocks the realm owner. Wake it before
        # synchronously asking that owner to perform the teardown.
        if not self.realm.executor.is_owner_thread():
            target = self._devtools_target
            if target is not None:
                stop = getattr(target, "_stop_pause_loop", None)
                if callable(stop):
                    stop()
        try:
            return self.realm.executor.call(self._close_owner)
        finally:
            self._resource_scope.close()
            with self._close_lock:
                self._closing = False

    def _close_owner(self):
        if self.closed:
            return
        if self._has_navigation:
            self.lifecycle.dispatch_beforeunload()
            self.lifecycle.dispatch_page_event("pagehide", self.window, {"bubbles": False, "persisted": False})
            self.lifecycle.dispatch_page_event("unload", self.window, {"bubbles": False})
        # Drop cached JS scripts before the V8 context goes away.
        self.scripts.clear_js_helpers()
        if self._devtools_target is not None:
            if self.context_owner._devtools is not None:
                self.context_owner._devtools.unregister(self)
            self._devtools_target = None
        self.closed = True
        self.tasks.close()
        self.resources.reset()
        self.lifecycle.load_waiters.clear()
        for frame in self._owned.drain("frames"):
            frame.close()
        for ws in self._owned.drain("websockets"):
            if self.network_backend:
                self.network_backend.close_websocket(ws)
        for worker in self._owned.drain("workers"):
            worker.fn_terminate()
        self.context_owner._discard_page(self)
        self.realm.close()


class BrowserContext:
    def __init__(self, browser):
        self.browser, self.pages = browser, []
        self.cookies = CookieJar()
        self.network_backend = CronetNetworkBackend(options=browser.cycronet_options) if browser.real_network else None
        if self.network_backend is not None:
            self.network_backend.cookies = self.cookies
        self._blob_urls = {}
        self.closed = False
        self._devtools = None

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

    # Context-level services exposed to realm objects.  Keeping these methods
    # here makes cookie/blob ownership explicit without exposing storage fields.
    @property
    def cookie_jar(self):
        return self.cookies

    def new_page(self, url="about:blank", *, hook=None, environment=None):
        self._ensure_open()
        page = Page(self, hook=hook, environment=environment)
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

    def start_devtools(self, page, *, host="127.0.0.1", port=0,
                       start_paused=False, startup_timeout=None):
        self._ensure_open()
        if page not in self.pages or page.closed:
            raise InvalidStateError("Page does not belong to this BrowserContext")
        if self._devtools is not None:
            raise InvalidStateError("BrowserContext already has DevTools attached")
        from .devtools import start_devtools
        handle = start_devtools(
            self, page, host=host, port=port,
            start_paused=start_paused, startup_timeout=startup_timeout,
        )
        self._devtools = handle._server
        return handle

    def close(self):
        if self.closed:
            return
        self.closed = True
        for page in list(self.pages):
            page.close()
        self.pages.clear()
        if self._devtools is not None:
            self._devtools.close()
            self._devtools = None
        self._blob_urls.clear()
        if self.network_backend:
            self.network_backend.close()
            self.network_backend = None


class Browser:
    def __init__(self, *, resource_loader=None,
                 real_network=False, cycronet_options=None, hook=False,
                 environment=None,
                 generate_interface_stubs=True, expose_unimplemented=True,
                 stub_logging="info", interface_baseline_path=None,
                 eager_interfaces=None, lazy_interfaces=True, strict_validation=False):
        started = time.perf_counter_ns()
        self._closed = False
        # pyv8 initializes its process-global V8 runtime during package import.
        self.contexts = []
        self.resource_loader = resource_loader
        self.real_network = bool(real_network)
        self.cycronet_options = dict(cycronet_options or {})
        self.hook = bool(hook)
        self.environment = BrowserEnvironment.from_value(environment)
        self.generate_interface_stubs = bool(generate_interface_stubs)
        self.expose_unimplemented = bool(expose_unimplemented)
        self.stub_logging = str(stub_logging or "info")
        self._startup_stats = {}
        snapshot = BaselineRepository.load(interface_baseline_path)
        loaded = time.perf_counter_ns()
        self.interface_universe = InterfaceUniverse(
            snapshot, registry=bootstrap_interfaces(),
            stub_logging=self.stub_logging,
            expose_unimplemented=self.expose_unimplemented,
            eager_interfaces=DEFAULT_EAGER_INTERFACES if eager_interfaces is None else eager_interfaces,
            strict_validation=strict_validation,
            lazy_interfaces=lazy_interfaces,
        )
        if strict_validation:
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
