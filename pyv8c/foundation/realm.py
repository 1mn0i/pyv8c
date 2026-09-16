"""Runtime contracts available to browser-facing interface objects.

Web API implementations need services from the realm an object lives in
(creating promises, converting errors, posting tasks). They consume them through
:class:`RealmServices` so it never imports ``platform`` or reaches into Page
privates.  The web layer is served by the other five narrow protocols, each
covering one slice of the realm a page exposes. ``PageServices`` assembles
the capability objects rather than implementing their protocols itself.

``RealmServices`` exposes task posting and script execution without importing
``platform``. Parameter conversion and Promise rejection belong to Web API
implementations, not to binding wrappers.
"""

from collections.abc import MutableSequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RealmServices(Protocol):
    """Identity, scripting and error materialisation of one JS realm."""

    url: str
    origin: Any
    closed: bool

    def post_task(self, callback) -> None: ...
    def promise_resolve(self, value: Any) -> Any: ...
    def evaluate_script(self, source: str) -> Any: ...
    def make_promise(self) -> Any:
        """Return ``(promise, resolve, reject)`` in this realm's context."""
        ...

    def js_error_value(self, exc: BaseException) -> Any:
        """Materialise a Python exception as a native JS error object."""
        ...


@runtime_checkable
class TaskServices(Protocol):
    """Task sources of one realm event loop."""

    closed: bool

    def post_task(self, callback) -> None: ...
    def schedule(self, callback, delay: float, repeat: bool = False) -> int: ...
    def cancel(self, ident: int) -> None: ...
    @property
    def microtasks(self) -> MutableSequence: ...
    def begin_network(self) -> None: ...
    def complete_network(self) -> None: ...
    def begin_worker(self) -> None: ...
    def complete_worker(self) -> None: ...


@runtime_checkable
class ResourceServices(Protocol):
    """Loading documents, subresources and worker sources for a realm."""

    def load_resource(self, url, request=None) -> Any: ...
    def load_resource_async(self, url, request, callback) -> Any: ...
    def resolve_worker_source(self, script_url: str) -> str: ...
    def load_iframe(self, iframe) -> Any: ...
    def discover_element_resource(self, node) -> None: ...


@runtime_checkable
class DocumentServices(Protocol):
    """Document, window and parser hooks of the realm's active document."""

    window: Any
    document: Any
    parsing: bool

    def element_class(self, tag: str) -> type: ...
    def install_inline_handler(self, node, name, source) -> None: ...
    def node_adopted(self, node) -> None: ...
    def parser_script_start(self, node) -> None: ...
    def parser_script_complete(self, node, source) -> None: ...
    def parser_is_blocked(self) -> bool: ...
    def write_markup(self, markup: str) -> bool: ...


@runtime_checkable
class WorkerServices(Protocol):
    """Spawning and tracking dedicated workers of one realm."""

    def worker_configuration(self) -> Any: ...
    def create_worker_realm(self, config) -> Any: ...
    def track_worker(self, worker) -> None: ...
    def discard_worker(self, worker) -> None: ...


@runtime_checkable
class ContextServices(Protocol):
    """Browser-context level services: blob URLs, cookies, networking."""

    def create_blob_url(self, blob, realm=None) -> str: ...
    def revoke_blob_url(self, url) -> None: ...
    def resolve_blob_url(self, url) -> Any: ...


__all__ = [
    "RealmServices", "TaskServices", "ResourceServices", "DocumentServices",
    "WorkerServices", "ContextServices", "realm_of", "post_or_call",
]


def realm_of(obj) -> Any:
    """Return the realm an interface object belongs to, or ``None``.

    Objects receive either a realm capability directly or the page's service
    bundle through injection (``_realm``) or ``_services``. The bundle keeps
    the web-facing capabilities together, while this helper returns only its
    ``realm_services`` facet to the binding engine.
    """
    services = getattr(obj, "_realm", None)
    if services is None:
        services = getattr(obj, "_services", None)
    return getattr(services, "realm_services", services)


def post_or_call(services, callback, *args) -> None:
    """Deliver ``callback`` through the task queue, or run it when there is none.

    Web API objects are routinely built without a realm -- by tests, and by the
    entry points that hand them a bare environment -- so they have no
    ``task_services`` to post to.  The DOM still has to make progress in that
    case, so the callback runs synchronously.  A ``None`` callback is a no-op,
    which is how the optional-callback APIs spell "the caller passed none".
    """
    if callback is None:
        return
    task_services = getattr(services, "task_services", None) if services is not None else None
    if task_services is None:
        callback(*args)
    else:
        task_services.post_task(lambda: callback(*args))
