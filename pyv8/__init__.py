"""Public Python facade for the native :mod:`pyv8._pyv8` extension."""

from __future__ import annotations

import os

from . import _pyv8
from .flag import *  # noqa: F401,F403 - flag constants are public API
from .tools import *  # noqa: F401,F403 - decorators/hooks are public API


_pyv8.init_v8(
    icudtl_path=os.path.join(os.path.dirname(__file__), "icudtl.dat"),
    params=["--expose-gc"],
)

_NATIVE_EXPORTS = (
    "Context", "Isolate", "Script", "Debugger", "JSObject", "JSValue",
    "JSPromise", "JSTypeArray", "JSFunction", "JSException",
    "JavaScriptTerminated", "Null", "Undefined", "current_context",
    "incumbent_context", "new", "v8_gc", "shutdown_v8", "v8_version",
)
globals().update({name: getattr(_pyv8, name) for name in _NATIVE_EXPORTS})

__version__ = "2.1.2"

from .devtools import start_devtools


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
    "FlagConstructor", "SideEffectType", "FlagArrayProto", "ConstructorConfig",
    "exposed_constructs",
    "__v8_construct_hook__", "__v8_get_hook__", "__v8_method_hook__", "__v8_set_hook__",
}
__all__ = [*_NATIVE_EXPORTS, "ArrayType", "start_devtools", "__version__"]
__all__ += sorted(_HELPER_EXPORTS)
