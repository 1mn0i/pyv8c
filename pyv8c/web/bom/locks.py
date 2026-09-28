"""The Web Locks API.

Nothing else here takes a lock, so every request is granted at once.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, callback_value, dictionary, dom_string, missing_error


@interface('Lock', None)
class Lock:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="mode", getter="get_mode", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="Lock",
        has_constructor=1,
    )

    def __init__(self, name="", mode="exclusive", **_):
        self._name, self._mode = str(name), str(mode)

    def get_name(self): return self._name
    def get_mode(self): return self._mode


@interface('LockManager', None)
class LockManager:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="request", callback="fn_request", length=2),
            MethodSpec(name="query", callback="fn_query", length=0),
        ),
        constructor_behavior=0,
        to_string_tag="LockManager",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services

    def fn_request(self, name=MISSING, callback=MISSING, options=None, *_extra):
        if name is MISSING:
            raise missing_error('LockManager', 'request', 2, 0, constructor=False)
        if callback is MISSING:
            raise missing_error('LockManager', 'request', 2, 1, constructor=False)
        named = name if isinstance(name, (list, tuple)) else dom_string(name)
        held = Lock(named, str(dictionary(options).get("mode") or "exclusive"))
        return self._on_task(lambda: callback(held))

    def fn_query(self, *_extra):
        return self._on_task(lambda: {"held": [], "pending": []})

    def _on_task(self, run):
        """Run on the task queue, like the browser, and carry the result into a promise."""
        services = self._services
        promise, resolve, reject = services.realm_services.make_promise()

        def task():
            try:
                resolve(run())
            except BaseException as error:
                reject(services.realm_services.js_error_value(error))

        services.task_services.schedule(task, 0, repeat=False)
        return promise


__all__ = ["Lock", "LockManager"]
