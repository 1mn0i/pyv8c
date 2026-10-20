"""Realm-owned rejection candidates and asynchronous DOM notification tasks."""


class RejectionTracker:
    def __init__(self, publish, post, state):
        self._publish = publish
        self._post = post
        self._state = state
        self._pending = {}
        self._queued = {}
        self._handled = {}
        self._pending_listener = None

    def set_pending_listener(self, listener):
        """Owner-thread subscription to nonempty checkpoint candidates.

        Replacing a subscriber first releases its registration. Queued DOM
        notifications are separate and do not keep this subscription active.
        """
        previous, self._pending_listener = self._pending_listener, listener
        if previous is not None and self._pending:
            previous(self, False)
        if listener is not None and self._pending:
            listener(self, True)

    def _pending_changed(self, pending):
        if self._pending_listener is not None:
            self._pending_listener(self, pending)

    def close(self):
        self.set_pending_listener(None)
        self._pending.clear()
        self._queued.clear()
        self._handled.clear()
        self._publish = None
        self._post = None
        if self._state is not None:
            self._state.close()
            self._state = None

    def record(self, event_type, _native_identity, promise, reason):
        if self._publish is None:
            return
        # V8 identity hashes can collide. The realm's private metadata supplies a
        # unique identity without keeping every rejected promise alive in Python.
        identity = self._state.identity(promise)
        if identity is None:  # V8 is terminating the active execution.
            return
        if event_type == 'unhandledrejection':
            first = not self._pending
            self._pending[identity] = (promise, reason)
            if first:
                self._pending_changed(True)
            return
        if self._pending.pop(identity, None) is not None:
            if not self._pending:
                self._pending_changed(False)
            return
        if self._queued.pop(identity, None) is not None:
            return
        notified, original_reason = self._state.take_notified(promise)
        if notified:
            self._handled[identity] = (promise, original_reason)
            self._submit(lambda: self._notify_handled(identity), self._handled, (identity,))

    def _submit(self, callback, entries, identities):
        try:
            accepted = self._post(callback)
        except BaseException:
            for identity in identities:
                entries.pop(identity, None)
            raise
        if accepted is False:
            for identity in identities:
                entries.pop(identity, None)

    def flush(self):
        """Checkpoint completion queues a task; it never fires a page event."""
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        self._pending_changed(False)
        self._queued.update(pending)
        identities = tuple(pending)
        self._submit(lambda: self._notify(identities), self._queued, identities)

    def _notify(self, identities):
        try:
            for identity in identities:
                entry = self._queued.get(identity)
                if entry is None or self._publish is None:
                    continue
                promise, reason = entry
                self._publish('unhandledrejection', promise, reason)
                # Event dispatch (including listener cleanup microtasks) can add
                # a catch. Only promises still unhandled after dispatch enter
                # the weak outstanding set and qualify for a handled event.
                if self._publish is not None and self._queued.pop(identity, None) is not None:
                    self._state.mark_notified(promise, reason)
        finally:
            for identity in identities:
                self._queued.pop(identity, None)

    def _notify_handled(self, identity):
        entry = self._handled.pop(identity, None)
        if entry is not None and self._publish is not None:
            self._publish('rejectionhandled', *entry)


__all__ = ['RejectionTracker']
