"""The timer and microtask surface every window-like global exposes.

``Window`` and ``WorkerGlobalScope`` share these algorithms, so the code lives
here and each global mixes it in; the IDL declarations stay with the interface
that exposes them.
"""

import pyv8

from ...foundation.arguments import (
    MISSING,
    callback_value,
    dom_string,
    integer,
    missing_error,
)


class _SchedulingMixin:
    """``setTimeout``/``setInterval``/``queueMicrotask`` for one global scope.

    Mixing this in requires ``task_services`` (timer queue) and ``realm_services``
    (scripting and microtask queue).
    """

    #: Interface name the missing-argument errors report.
    _scheduling_interface = "Window"

    def fn_setTimeout(self, callback=MISSING, delay=0, *arguments):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'setTimeout', 1, 0, constructor=False)
        return self._schedule(callback, delay, arguments, repeat=False)

    def fn_setInterval(self, callback=MISSING, delay=0, *arguments):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'setInterval', 1, 0, constructor=False)
        return self._schedule(callback, delay, arguments, repeat=True)

    def _schedule(self, callback, delay, arguments, *, repeat):
        """Queue one timer, accepting the string form HTML also allows."""
        if not callable(callback):
            source = dom_string(callback)

            def callback(): return self._services.realm_services.evaluate_script(source)
        elif arguments:
            original = callback

            def callback(): return original(*arguments)
        if delay is pyv8.Undefined:
            delay = 0
        delay = integer(delay, signed=True)
        return self._services.task_services.schedule(callback, float(delay) / 1000.0, repeat=repeat)

    def fn_clearTimeout(self, ident=0, *_extra):
        self._cancel_timer(ident)

    def fn_clearInterval(self, ident=0, *_extra):
        self._cancel_timer(ident)

    def fn_requestAnimationFrame(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'requestAnimationFrame', 1, 0, constructor=False)
        # No compositor drives frames, so a frame request is a timer due
        # immediately, which is what the callback-observable behaviour reduces to.
        return self._services.task_services.schedule(callback_value(callback), 0, repeat=False)

    def fn_cancelAnimationFrame(self, ident=0, *_extra):
        self._cancel_timer(ident)

    def fn_queueMicrotask(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'queueMicrotask', 1, 0, constructor=False)
        self._services.realm_services.enqueue_microtask(callback_value(callback))

    def _cancel_timer(self, ident):
        self._services.task_services.cancel(int(integer(ident, signed=True)))


__all__ = ["_SchedulingMixin"]
