"""Public Python facade for the native :mod:`pyv8._pyv8` extension."""

from __future__ import annotations

import os

from . import _pyv8
from .flag import *  # noqa: F401,F403 - flag constants are public API



_pyv8.init_v8(
    icudtl_path=os.path.join(os.path.dirname(__file__), "icudtl.dat"),
    # params=["--expose-gc"],
)

_NATIVE_EXPORTS = (
    "IdlSpec", "AttributeSpec", "MethodSpec", "ConstantSpec", "ConstantCallbackSpec", "Context", "Isolate", "Script", "Debugger", "JSObject", "JSValue",
    "JSPromise", "JSTypeArray", "JSFunction", "JSException",
    "JavaScriptTerminated", "Null", "Undefined", "current_context",
    "incumbent_context", "new", "v8_gc", "shutdown_v8", "v8_version",
)
globals().update({name: getattr(_pyv8, name) for name in _NATIVE_EXPORTS})

__version__ = "2.1.2"

from .devtools import DevToolsHandle, DevToolsOptions, start_devtools
from .trace import format_trace, log_trace


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


_HELPER_EXPORTS = {
    "CallbackType", "FlagImmutable", "V8Attribute", "FlagLocation",
    "FlagReceiverCheck", "FlagCrossOriginCheck", "FlagExposed",
    "FlagConstructor", "SideEffectType", "FlagArrayProto",
    "format_trace", "log_trace",
}
__all__ = [*_NATIVE_EXPORTS, "ArrayType", "DevToolsOptions", "DevToolsHandle", "start_devtools", "__version__"]
__all__ += sorted(_HELPER_EXPORTS)
