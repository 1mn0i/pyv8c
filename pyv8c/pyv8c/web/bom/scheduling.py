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
from ...foundation.logging import logger


class _SchedulingMixin:
    """``setTimeout``/``setInterval``/``queueMicrotask`` for one global scope.

    Mixing this in requires ``task_services`` (timer queue) and ``realm_services``
    (scripting and microtask queue).
    """

    #: IdlInterface name the missing-argument errors report.
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
        name = "setInterval" if repeat else "setTimeout"
        source = None
        if not callable(callback):
            source = dom_string(callback)

            def body(): return self._services.realm_services.evaluate_script(source)
        elif arguments:
            def body(): return callback(*arguments)
        else:
            body = callback
        if delay is pyv8.Undefined:
            delay = 0
        milliseconds = float(integer(delay, signed=True))
        ident = 0

        def run():
            logger.debug("{} {} runs", name, ident)
            return body()

        ident = self._services.task_services.schedule_html_timer(run, milliseconds / 1000.0, repeat=repeat)
        logger.debug("{} {} scheduled in {} ms ({})", name, ident, milliseconds,
                     "string" if source is not None else "function")
        return ident

    def fn_clearTimeout(self, ident=0, *_extra):
        self._cancel_timer(ident, "clearTimeout")

    def fn_clearInterval(self, ident=0, *_extra):
        self._cancel_timer(ident, "clearInterval")

    def fn_requestAnimationFrame(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'requestAnimationFrame', 1, 0, constructor=False)
        return self._services.task_services.request_animation_frame(callback_value(callback))

    def fn_cancelAnimationFrame(self, ident=0, *_extra):
        value = int(integer(ident, signed=True))
        self._services.task_services.cancel_animation_frame(value)

    def fn_queueMicrotask(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'queueMicrotask', 1, 0, constructor=False)
        self._services.realm_services.enqueue_microtask(callback_value(callback))

    def _cancel_timer(self, ident, name):
        value = int(integer(ident, signed=True))
        logger.debug("{} {}", name, value)
        self._services.task_services.cancel(value)

__all__ = ["_SchedulingMixin"]
