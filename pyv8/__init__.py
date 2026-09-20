"""Public Python facade for the native :mod:`pyv8._pyv8` extension."""

from __future__ import annotations

import os

from . import _pyv8
from . import flag as _flag
from .flag import *  # noqa: F401,F403 - flag constants are public API


_pyv8.init_v8(
    icudtl_path=os.path.join(os.path.dirname(__file__), "icudtl.dat"),
    # params=["--expose-gc"],
)

# Everything the extension publishes, less what this package owns itself.  Derived
# rather than listed: a name added to the extension is exported here without anyone
# having to remember it, and the tests check that the two stay in step.  init_v8 is
# the one that stays private -- this module has already called it by now.
_INTERNAL_NATIVE = {"init_v8"}
_NATIVE_EXPORTS = tuple(
    sorted(
        name
        for name in dir(_pyv8)
        if not name.startswith("_") and name not in _INTERNAL_NATIVE
    )
)
globals().update({name: getattr(_pyv8, name) for name in _NATIVE_EXPORTS})

__version__ = "2.1.2"

# These two come after init_v8 on purpose: both reach back into this package, and
# what they need of the extension has to exist by then.  The `as` form is what
# tells a reader -- and pyflakes -- that the names are re-exported, not unused.
from .devtools import (  # noqa: E402
    DevToolsHandle as DevToolsHandle,
    DevToolsOptions as DevToolsOptions,
    start_devtools as start_devtools,
)
from .trace import (  # noqa: E402,F401 - re-exported, not called from here
    format_trace as format_trace,
    log_trace as log_trace,
)


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


# The flag constants come from flag.py, which is also where a new one is declared,
# so its __all__ is the list rather than a copy of it kept here.
_HELPER_EXPORTS = set(_flag.__all__) | {"format_trace", "log_trace"}
__all__ = [
    *_NATIVE_EXPORTS,
    "ArrayType",
    "DevToolsOptions",
    "DevToolsHandle",
    "start_devtools",
    "__version__",
]
__all__ += sorted(_HELPER_EXPORTS)
