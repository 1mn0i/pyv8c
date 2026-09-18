"""The specification's unhandled-rejection algorithm, driven by V8.

V8 notices a rejection without a handler as soon as the promise is rejected --
before the turn that rejected it has finished -- and revokes that notice if a
handler appears before the microtask checkpoint ends.  Publishing
``unhandledrejection`` at that first notice would report every
``Promise.reject(...).catch(...)`` written on one line, which no browser does.

A notice is therefore a *candidate*: the tracker holds it until the realm's
owner thread is back in Python, which is when the checkpoint is over
(``RealmExecutor.attach_rejection_tracker``), and publishes then.  A revocation
arriving before that point drops the candidate; one arriving afterwards is the
specification's ``rejectionhandled``.

It lives in ``foundation`` because a page and a worker both need it and the
worker packages may not import ``platform``.
"""


class RejectionTracker:
    """Queue one realm's unhandled-rejection candidates."""

    def __init__(self, publish):
        #: Called as ``publish(event_type, promise, reason)``.
        self._publish = publish
        #: Candidates by promise identity, as ``(promise, reason)``.
        self._pending = {}
        #: ``rejectionhandled`` notifications waiting for the same boundary.
        self._followups = []

    def close(self):
        self._pending.clear()
        self._followups.clear()
        self._publish = None

    def record(self, event_type, identity, promise, reason):
        """Take one notification from the isolate's rejection hook."""
        if self._publish is None:
            return
        if event_type == "unhandledrejection":
            self._pending[identity] = (promise, reason)
            return
        # A handler was attached after V8 reported the rejection.
        if self._pending.pop(identity, None) is not None:
            # Still queued, so the page never saw the rejection: this is the
            # ordinary case of a handler attached within the same turn.
            return
        # The rejection was published, so the paired event is owed.  It is
        # queued rather than published here so that both rejection events reach
        # the page from the same boundary, always in the specification's order.
        self._followups.append((promise, reason))

    def flush(self):
        """Publish what the turn that just ended left owing the page.

        Called after every command and every turn, so the common case -- nothing
        pending -- has to be the cheap one: a realm that never rejects anything
        pays an ``is``-check per unit of work and no allocation.
        """
        if not self._pending and not self._followups:
            return
        pending, self._pending = self._pending, {}
        followups, self._followups = self._followups, []
        for promise, reason in pending.values():
            if self._publish is None:
                return
            self._publish("unhandledrejection", promise, reason)
        for promise, reason in followups:
            if self._publish is None:
                return
            self._publish("rejectionhandled", promise, reason)


__all__ = ["RejectionTracker"]
