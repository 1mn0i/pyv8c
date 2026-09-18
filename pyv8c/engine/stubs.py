"""Generated API shells for baseline interfaces without a hand-written class.

Generated stubs carry ``__v8_idl_stub__`` for Python diagnostics. Native
installation treats them exactly like other explicit callbacks.  Logging is controlled per :class:`InterfaceUniverse`
via ``stub_logging``.
"""

from __future__ import annotations

import v8n
from loguru import logger


def mark_stub(callback):
    """Mark generated placeholders for Python diagnostics only."""
    callback.__v8_idl_stub__ = True
    return callback


class StubFactory:
    """Build the safe, logging shells used for unimplemented members."""

    def __init__(self, stub_logging="info"):
        self.stub_logging = str(stub_logging or "info").lower()

    def _log(self, message, *args):
        if self.stub_logging not in ("off", "none", "disabled"):
            logger.info(message, *args)

    def method(self, interface_name, member_name, *, static=False):
        def callback(*args):
            self._log("pyv8c: {}.{}() 待实现", interface_name, member_name)
            return v8n.Undefined
        callback.__name__ = f"_pyv8c_stub_method_{interface_name}_{member_name}"
        return mark_stub(callback)

    def getter(self, interface_name, member_name):
        def getter(*_args):
            self._log("pyv8c: {}.{} getter 待实现", interface_name, member_name)
            return v8n.Undefined
        getter.__name__ = f"_pyv8c_stub_get_{interface_name}_{member_name}"
        return mark_stub(getter)

    def setter(self, interface_name, member_name):
        def setter(*args):
            self._log("pyv8c: {}.{} setter 待实现", interface_name, member_name)
        setter.__name__ = f"_pyv8c_stub_set_{interface_name}_{member_name}"
        return mark_stub(setter)

    def constructor(self, interface_name):
        def constructor(_self, *_args, **_kwargs):
            self._log("pyv8c: {} constructor 待实现", interface_name)
        constructor.__name__ = interface_name
        constructor.__v8_idl_stub__ = True
        return constructor

    def call_handler(self, interface_name):
        def call_handler(*_args):
            return v8n.Undefined
        call_handler.__name__ = f"_pyv8c_stub_call_{interface_name}"
        return mark_stub(call_handler)

    def handler(self, interface_name, method_name):
        def handler(*args):
            # Returning None tells V8 that the interceptor did not handle it;
            # enum interceptors report "nothing to enumerate" with an empty tuple.
            if method_name.endswith("enum__"):
                return ()
            return None
        handler.__name__ = f"_pyv8c_stub_{method_name}_{interface_name}"
        return mark_stub(handler)
