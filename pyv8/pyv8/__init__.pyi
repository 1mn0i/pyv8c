"""Type surface for ``import pyv8``.

The native half of the package is the extension module, so the names it publishes
are the ones ``_pyv8.pyi`` declares; this stub hands them out where ``import pyv8``
looks, and names the pieces that live in Python.
"""

from ._pyv8 import *  # noqa: F401,F403 - the extension's surface, declared in its own stub
from .devtools import (
    DevToolsHandle as DevToolsHandle,
    DevToolsOptions as DevToolsOptions,
    start_devtools as start_devtools,
)
from .flag import *  # noqa: F401,F403 - flag constants are public API
from .trace import format_trace as format_trace, log_trace as log_trace

from typing import Any

#: The extension's ``Context``, named for what it is: V8's context.
V8Context = Context

__version__: str

def transfer(value: Any, target_context: Context) -> Any:
    """Copy a value's structure into another isolate's realm."""
    ...

class ArrayType:
    """Numeric identifiers accepted by :class:`JSTypeArray`."""

    Uint32Array: int
    Uint16Array: int
    Uint8Array: int
    Int32Array: int
    Int16Array: int
    Int8Array: int
    Float64Array: int
    Float32Array: int
    Uint8ClampedArray: int
