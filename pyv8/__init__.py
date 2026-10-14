"""Public Python facade for the native :mod:`pyv8._pyv8` extension."""

from __future__ import annotations

from importlib import import_module

from . import _pyv8
from . import flag as _flag
from .flag import *  # noqa: F401,F403 - flag constants are public API


# Importing this package starts no V8.  The extension registers its types, records
# the icudtl.dat sitting beside it, and waits; the runtime comes up on the first call
# that needs it -- Context, Isolate, Script, new, current_context, v8_gc -- and
# pyv8.configure() is how a caller says what that startup should use.  Nothing added
# here may touch the engine at import time.

# Everything the extension publishes, less what this package owns itself.  Derived
# rather than listed: a name added to the extension is exported here without anyone
# having to remember it, and the tests check that the two stay in step.  init_v8 is
# the one that stays private -- configure() is its public face, and a second way to
# name the ICU data file would be one too many.
_INTERNAL_NATIVE = {"init_v8"}
_NATIVE_EXPORTS = tuple(
    sorted(
        name
        for name in dir(_pyv8)
        if not name.startswith("_") and name not in _INTERNAL_NATIVE
    )
)
globals().update({name: getattr(_pyv8, name) for name in _NATIVE_EXPORTS})

#: The type the extension calls ``Context``, named for what it is: V8's context.
V8Context = Context

__version__ = "2.1.2"

# DevTools and the trace helpers are re-exported, not imported: the service brings a
# websocket server and its asyncio tail with it, which a process that never debugs
# should not pay for at import.  They load on first attribute access.
_LAZY_EXPORTS = {
    "devtools": ".devtools",
    "DevToolsHandle": ".devtools",
    "DevToolsOptions": ".devtools",
    "start_devtools": ".devtools",
    "trace": ".trace",
    "format_trace": ".trace",
    "log_trace": ".trace",
}


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = module if name == module_name.lstrip(".") else getattr(module, name)
    globals()[name] = value
    return value


class ArrayType:
    """Numeric identifiers accepted by :class:`JSTypeArray`."""

    Uint32Array = 1
    Uint16Array = 2
    Uint8Array = 3
    Int32Array = 4
    Int16Array = 5
    Int8Array = 6
    Float64Array = 7
    Float32Array = 8
    Uint8ClampedArray = 9
    BigInt64Array = 10
    BigUint64Array = 11


# The flag constants come from flag.py, which is also where a new one is declared,
# so its __all__ is the list rather than a copy of it kept here.
_HELPER_EXPORTS = set(_flag.__all__) | {"format_trace", "log_trace", "transfer"}

#: Values that cross an isolate boundary as they are.
_TRANSFERABLE_LEAVES = (bool, int, float, str, bytes, bytearray)

_NOT_TRANSFERABLE = (
    "{} cannot cross isolates; only primitives, arrays and plain objects"
    " can be transferred"
)


def _crossable(value):
    """The structure of *value*, or an error naming what cannot cross."""
    if value is None or value is Undefined or value is Null:
        return value
    if type(value) in _TRANSFERABLE_LEAVES:
        return value
    if isinstance(value, JSObject):
        # `to_python` materialises arrays and plain objects; anything else comes
        # back as a handle, which is what cannot cross.
        materialized = value.to_python()
        if isinstance(materialized, JSObject):
            raise TypeError(_NOT_TRANSFERABLE.format(type(value).__name__))
        return _crossable(materialized)
    if isinstance(value, list):
        return [_crossable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_crossable(item) for item in value)
    if isinstance(value, dict):
        return {key: _crossable(item) for key, item in value.items()}
    raise TypeError(_NOT_TRANSFERABLE.format(type(value).__name__))


def transfer(value, target_context):
    """Copy *value* into another isolate's realm.

    Only structure crosses: primitives, arrays and plain objects.  A function, a
    Promise, a typed array or a host object has no meaning in the other isolate's
    heap, so it is refused instead of being wrapped in a handle that would fail
    on first use.
    """
    return target_context.to_js(_crossable(value))
__all__ = [
    *_NATIVE_EXPORTS,
    "ArrayType",
    "V8Context",
    "DevToolsOptions",
    "DevToolsHandle",
    "start_devtools",
    "__version__",
]
__all__ += sorted(_HELPER_EXPORTS)
