"""Runtime contracts available to browser-facing interface objects."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RealmServices(Protocol):
    closed: bool
    url: str

    def post_task(self, callback) -> None: ...
    def promise_resolve(self, value: Any) -> Any: ...
    def load_resource(self, url, request=None) -> Any: ...
