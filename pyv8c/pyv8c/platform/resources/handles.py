"""Cancellation and completion for a page-owned load, independent of transport."""
from threading import RLock


class LoadHandle:
    """One terminal transition; transport binding is safe before or after cancel.

    Transport cancellation runs outside the lock because it may invoke callbacks.
    ``done`` includes cancellation; document readiness is tracked separately.
    """

    __slots__ = ("_lock", "_state", "_transport", "_callbacks")

    def __init__(self):
        self._lock = RLock()
        self._state = "pending"
        self._transport = None
        self._callbacks = []

    def add_done_callback(self, callback):
        with self._lock:
            if self._state == "pending":
                self._callbacks.append(callback)
                return
        callback(self)

    @property
    def cancelled(self):
        with self._lock:
            return self._state == "cancelled"

    @property
    def done(self):
        with self._lock:
            return self._state != "pending"

    def bind_transport(self, transport):
        with self._lock:
            cancelled = self._state == "cancelled"
            if self._state == "pending":
                self._transport = transport
        if cancelled and transport is not None:
            transport.cancel()

    def cancel(self):
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "cancelled"
            transport, self._transport = self._transport, None
            callbacks, self._callbacks = self._callbacks, []
        try:
            if transport is not None:
                transport.cancel()
        finally:
            for callback in callbacks:
                callback(self)
        return True

    def finish(self):
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "finished"
            self._transport = None
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback(self)
        return True


