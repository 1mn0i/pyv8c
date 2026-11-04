"""Realm-owned, non-executing native promise bookkeeping."""


class WeakPromiseState:
    """Private metadata on each promise never roots the promise or runs page JS."""
    __slots__ = ('_context',)

    def __init__(self, context):
        self._context = context

    def identity(self, promise):
        return self._context._promise_rejection_state(promise, 0)

    def mark_notified(self, promise, _reason):
        self._context._promise_rejection_state(promise, 1)

    def take_notified(self, promise):
        result = self._context._promise_rejection_state(promise, 2)
        return (False, None) if result is None else result

    def close(self):
        self._context = None
