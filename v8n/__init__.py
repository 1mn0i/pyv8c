"""Public Python facade for the native :mod:`v8n._v8n` extension.

The extension is deliberately thin: it exposes the V8 platform and the object
model, and nothing about pages, interfaces or the host.  Everything a host
builds on top of it -- realms, IDL registration, trace formatting -- is added
here, in Python, where it can be read and tested without a compiler.
"""

from __future__ import annotations

import os

from . import _v8n
from .flag import *  # noqa: F401,F403 - flag constants are public API
from .flag import __all__ as _FLAG_EXPORTS
from .idl import (
    AttributeSpec,
    ConstantCallbackSpec,
    ConstantSpec,
    IdlSpec,
    MethodSpec,
)
from .idl import __all__ as _IDL_EXPORTS
from .trace import format_trace, log_trace

__version__ = "0.1.0"

#: The ICU data the extension was built against, shipped next to the module.
_ICU_DATA = os.path.join(os.path.dirname(__file__), "icudtl.dat")

#: V8 flags for this process, whitespace separated.  They are process-global and
#: can only be applied before V8 starts, which import does, so this is the only
#: chance a host gets to influence them without importing the extension first.
_FLAGS = tuple((os.environ.get("V8N_FLAGS") or "").split())

# V8 is process-global and its platform has to exist before the first isolate,
# so importing the package is the host's declaration of intent and the platform
# starts here.  shutdown() is the explicit counterpart; it is only meaningful
# once every isolate is gone.  When icudtl.dat is missing the extension reports
# it, because the digest is compiled in.
_v8n.initialize(
    icudtl_path=_ICU_DATA if os.path.exists(_ICU_DATA) else None,
    flags=_FLAGS,
)

v8_version = _v8n.v8_version
shutdown = _v8n.shutdown
is_initialized = _v8n.is_initialized
microtask_queues_supported = _v8n.microtask_queues_supported
set_error_map = _v8n.set_error_map

# The type surface.  Kept as explicit aliases rather than a star import so that
# a name disappearing from the extension is a missing attribute here, not a
# silently absent export.
Isolate = _v8n.Isolate
Realm = _v8n.Realm
Value = _v8n.Value
Null = _v8n.Null
Undefined = _v8n.Undefined

__all__ = [
    "__version__",
    "v8_version",
    "shutdown",
    "is_initialized",
    "microtask_queues_supported",
    "set_error_map",
    "Isolate",
    "Realm",
    "Value",
    "Null",
    "Undefined",
    "format_trace",
    "log_trace",
    *_IDL_EXPORTS,
    *_FLAG_EXPORTS,
]
