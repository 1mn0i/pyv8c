"""Type stubs for the native ``v8n._v8n`` extension."""

from __future__ import annotations

from collections.abc import Sequence

def v8_version() -> str:
    """Return the V8 version this extension was built against."""

def initialize(
    icudtl_path: str | None = ...,
    flags: Sequence[str] = ...,
) -> None:
    """Start the process-wide V8 platform.  Idempotent."""

def shutdown() -> None:
    """Dispose the process-wide V8 platform.  No isolate may exist."""

def is_initialized() -> bool:
    """Return whether the process-wide V8 platform is running."""

def icu_digest() -> str:
    """Return the ICU data digest this extension requires, or an empty string."""

def microtask_queues_supported() -> bool:
    """Return whether this build can use v8::MicrotaskQueue."""

def set_error_map(mapping: dict[str, type | object] | None) -> None:
    """Register which Python class each JavaScript error name becomes."""

class Isolate:
    """An independent V8 isolate owned by its creating thread.

    Only the creating thread may use it: every operation checks the caller and
    raises RuntimeError otherwise.  Closing is explicit and idempotent.
    """

    def __init__(self) -> None: ...
    @property
    def closed(self) -> bool:
        """Whether the isolate has been destroyed."""
    @property
    def owner_thread(self) -> int:
        """Identity of the thread that created it."""
    @property
    def live_handles(self) -> int:
        """How many V8 handles this isolate still retains."""
    def close(self) -> bool:
        """Destroy the isolate.  Idempotent; owner thread only."""
    def check_thread(self) -> None:
        """Raise unless the calling thread is this isolate's owner."""

class Value:
    """A retained JavaScript value, usable while its realm is open.

    A value crossing out of JavaScript is a primitive, the original Python host
    object, or one of these.  Nothing is expanded on the way out, so reading a
    property, coercing the value and calling it are separate operations a host
    asks for.
    """

    @property
    def is_valid(self) -> bool:
        """Whether the realm that produced this handle is still open."""
    @property
    def is_array(self) -> bool:
        """Whether the value is a JavaScript array."""
    @property
    def is_callable(self) -> bool:
        """Whether the value is a JavaScript function."""
    def to_python(self) -> object:
        """Materialise arrays and plain objects recursively.

        Cycles and shared references are preserved, and a getter that throws
        propagates.  Functions, typed arrays and host objects stay handles.
        """
    def get(self, name: str) -> object:
        """Read one JavaScript property by name.

        Prefer this to ``getattr`` for an arbitrary name, because a Python
        attribute of the wrapper wins there.
        """
    def delete(self, name: str) -> bool:
        """Delete one property, answering whether JavaScript deleted it."""
    def keys(self) -> tuple[str, ...]:
        """The value's own enumerable string keys."""
    def iterator(self) -> object:
        """The value's ``[Symbol.iterator]``, or ``undefined``."""
    def call(self, receiver: object, *args: object) -> object:
        """Call the value with an explicit receiver; ``None`` is undefined."""
    def to_string(self) -> str:
        """``String(value)``."""
    def to_number(self) -> float:
        """``Number(value)``; NaN rather than an error, and a TypeError for a BigInt."""
    def to_boolean(self) -> bool:
        """``Boolean(value)``."""
    def __call__(self, *args: object) -> object:
        """Call the value with the ``undefined`` receiver."""
    def __getattr__(self, name: str) -> object:
        """Read a JavaScript property; a Python attribute wins if there is one."""
    def __setattr__(self, name: str, value: object) -> None:
        """Write a JavaScript property."""
    def __repr__(self) -> str: ...

class Realm:
    """One JavaScript global and everything installed into it.

    Only the isolate's owner thread may use it.  Closing is explicit and
    idempotent, and releases every handle the realm produced.
    """

    def __init__(
        self,
        isolate: Isolate | None = ...,
        name: str | None = ...,
        microtasks: bool = ...,
        timeout: int = ...,
        global_this: object | None = ...,
        description: dict | None = ...,
    ) -> None: ...
    @property
    def closed(self) -> bool:
        """Whether the realm has been closed."""
    @property
    def is_active(self) -> bool:
        """Whether the realm still accepts entry."""
    @property
    def glob(self) -> Value:
        """The realm's global object, as a handle."""
    @property
    def isolate(self) -> Isolate | None:
        """The isolate this realm runs on."""
    @property
    def microtasks(self) -> bool:
        """Whether this realm can run microtasks."""
    @property
    def root_count(self) -> int:
        """How many Python references this realm holds.  Zero after a close."""
    @property
    def timeout(self) -> int:
        """Wall-clock budget for one entry, in milliseconds.  0 means none."""
    @timeout.setter
    def timeout(self, value: int) -> None: ...
    @property
    def trace_hook(self) -> object | None:
        """Called with ``(event, member, detail)`` for every dispatched access."""
    @trace_hook.setter
    def trace_hook(self, value: object | None) -> None: ...
    @property
    def terminated(self) -> bool:
        """Whether the last entry was stopped by its timeout."""
    def evaluate(self, source: str, filename: str | None = ...) -> object:
        """Compile and run source through the three-state boundary."""
    def install(self, description: dict) -> None:
        """Install a whole batch of interfaces in one crossing.

        The description is validated and every template is built before anything
        is written, so a description that is wrong installs nothing.
        """
    def wrap(self, object: object) -> Value:
        """A handle on a Python object's JavaScript counterpart."""
    def close(self) -> bool:
        """Release everything the realm owns.  Idempotent."""
    def set_error_hooks(
        self,
        on_uncaught: object | None = ...,
        on_rejection: object | None = ...,
    ) -> None:
        """Install (or remove) the hooks for failures V8 handles on its own."""
    def enqueue_microtask(self, callable: object) -> None:
        """Queue a callable to run at the next microtask checkpoint."""
    def run_microtasks(self) -> bool:
        """Drain the microtask queue."""

Null: object
"""The single JavaScript ``null``, which converts to itself in both directions."""

Undefined: object
"""The single JavaScript ``undefined``, and the answer for a missing property."""
