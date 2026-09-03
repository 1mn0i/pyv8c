"""Generated API shells for baseline interfaces without a hand-written class.

Every stub is a direct callback marked ``__v8_idl_stub__`` so the native
bridge can distinguish "declared but unimplemented" members from real
implementations.  Logging is controlled per :class:`InterfaceUniverse`
via ``stub_logging``.
"""

from __future__ import annotations

import pyv8
from loguru import logger

from .webidl import validate_arguments


def mark_direct(callback, *, stub=False):
    """Flag a callback so the native bridge skips generic conversion."""
    callback.__v8_direct_callback__ = True
    if stub:
        callback.__v8_idl_stub__ = True
    return callback


class StubFactory:
    """Build the safe, logging shells used for unimplemented members."""

    def __init__(self, stub_logging="info"):
        self.stub_logging = str(stub_logging or "info").lower()

    def _log(self, message, *args):
        if self.stub_logging not in ("off", "none", "disabled"):
            logger.info(message, *args)

    def method(self, interface_name, member_name, plan, *, static=False):
        def callback(*args):
            values = args if static else args[1:]
            if plan.resolved:
                validate_arguments(plan, values)
            self._log("pyv8c: {}.{}() 待实现", interface_name, member_name)
            return pyv8.Undefined
        callback.__name__ = f"_pyv8c_stub_method_{interface_name}_{member_name}"
        return mark_direct(callback, stub=True)

    def getter(self, interface_name, member_name):
        def getter(*_args):
            self._log("pyv8c: {}.{} getter 待实现", interface_name, member_name)
            return pyv8.Undefined
        getter.__name__ = f"_pyv8c_stub_get_{interface_name}_{member_name}"
        return mark_direct(getter, stub=True)

    def setter(self, interface_name, member_name, plan):
        def setter(*args):
            values = args[1:] if len(args) > 1 else args
            if plan.resolved:
                validate_arguments(plan, values)
            self._log("pyv8c: {}.{} setter 待实现", interface_name, member_name)
        setter.__name__ = f"_pyv8c_stub_set_{interface_name}_{member_name}"
        return mark_direct(setter, stub=True)

    def constructor(self, interface_name):
        def constructor(_self, *_args, **_kwargs):
            self._log("pyv8c: {} constructor 待实现", interface_name)
        constructor.__name__ = interface_name
        constructor.__v8_idl_stub__ = True
        return constructor

    def call_handler(self, interface_name):
        def call_handler(*_args):
            return pyv8.Undefined
        call_handler.__name__ = f"_pyv8c_stub_call_{interface_name}"
        return mark_direct(call_handler, stub=True)

    def handler(self, interface_name, method_name):
        def handler(*args):
            # Returning None tells V8 that the interceptor did not handle it.
            if method_name.endswith("enum__"):
                return ()
            if method_name.endswith("query__") or method_name.endswith("del__"):
                return None
            return None
        handler.__name__ = f"_pyv8c_stub_{method_name}_{interface_name}"
        return mark_direct(handler, stub=True)
