"""Runtime contracts available to browser-facing interface objects.

``engine`` needs a small set of services from the realm an object lives in
(creating promises, converting errors, posting tasks). It consumes them through
:class:`RealmServices` so it never imports ``platform`` or reaches into Page
privates. ``PageServices`` (and future worker scopes) implement the protocol.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RealmServices(Protocol):
    closed: bool
    url: str

    def post_task(self, callback) -> None: ...
    def promise_resolve(self, value: Any) -> Any: ...
    def load_resource(self, url, request=None) -> Any: ...
    def make_promise(self) -> Any:
        """Return ``(promise, resolve, reject)`` in this realm's context."""
        ...

    def js_error_value(self, exc: BaseException) -> Any:
        """Materialise a Python exception as a native JS error object."""
        ...

    def worker_configuration(self) -> Any: ...
    def create_worker_realm(self, config) -> Any: ...


@runtime_checkable
class ResourceServices(Protocol):
    def load_resource(self, url, request=None) -> Any: ...
    def load_resource_async(self, url, request, callback) -> None: ...


@runtime_checkable
class ContextServices(Protocol):
    def create_blob_url(self, blob, realm=None) -> str: ...
    def revoke_blob_url(self, url) -> None: ...
    def resolve_blob_url(self, url) -> Any: ...


__all__ = ["RealmServices", "ResourceServices", "ContextServices", "realm_of"]


def realm_of(obj) -> Any:
    """Return the realm an interface object belongs to, or ``None``.

    Objects receive the realm through injection (``_realm``, used by the
    binding engine when constructing instances) or as ``_services`` (the
    PageServices surface handed in by platform code). Both point at the
    same :class:`RealmServices` implementation.
    """
    realm = getattr(obj, "_realm", None)
    if realm is not None:
        return realm
    return getattr(obj, "_services", None)
