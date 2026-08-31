"""Runtime contracts available to browser-facing interface objects.

``bindings`` needs a small set of services from the realm an object lives in
(creating promises, converting errors, posting tasks). It consumes them through
:class:`RealmServices` so it never imports ``runtime`` or reaches into Page
privates. ``Page`` and future worker scopes implement the protocol.
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


def realm_of(obj) -> Any:
    """Return the realm an interface object belongs to, or ``None``.

    Objects receive the realm through injection (``_realm``) or, for
    historically page-bound types, as ``_page``. Both point at the same
    :class:`RealmServices` implementation.
    """
    realm = getattr(obj, "_realm", None)
    if realm is not None:
        return realm
    return getattr(obj, "_page", None)
