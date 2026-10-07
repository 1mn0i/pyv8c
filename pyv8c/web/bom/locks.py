"""The Web Locks API.

Nothing else here takes a lock, so every request is granted at once.
"""

from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, missing_error
from ..dom.events import Event, EventTarget
from .utils import settled


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


@interface('WakeLockSentinel', 'EventTarget')
class WakeLockSentinel(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="released", getter="get_released", side_effect=1),
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="onrelease", getter="get_onrelease", setter="set_onrelease"),
        ),
        methods=(MethodSpec(name="release", callback="fn_release", length=0, side_effect=1),),
        constructor_behavior=0,
        to_string_tag="WakeLockSentinel",
        has_constructor=1,
    )

    def __init__(self, kind="screen", services=None, **_):
        super().__init__(services=services)
        self._kind = str(kind)
        self._released = False

    def get_released(self): return self._released
    def get_type(self): return self._kind
    def get_onrelease(self): return self._get_handler("onrelease")
    def set_onrelease(self, value): self._set_handler("onrelease", value)
    def fn_release(self, *_extra):
        if not self._released:
            self._released = True
            self.fn_dispatchEvent(Event("release"))
        return settled(self._services, value=None)


@interface('WakeLock', None)
class WakeLock:
    """A request is granted with a sentinel; nothing here is kept awake."""

    __v8_idl_spec__ = IdlSpec(
        methods=(MethodSpec(name="request", callback="fn_request", length=1, side_effect=1),),
        constructor_behavior=0,
        to_string_tag="WakeLock",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services

    def fn_request(self, kind=MISSING, *_extra):
        if kind is MISSING:
            raise missing_error('WakeLock', 'request', 1, 0, constructor=False)
        return settled(self._services, value=WakeLockSentinel(kind=dom_string(kind),
                                                              services=self._services))


__all__ = ["Lock", "LockManager", "WakeLock", "WakeLockSentinel"]
