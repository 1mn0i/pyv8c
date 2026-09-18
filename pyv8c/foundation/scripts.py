"""The JavaScript helpers one realm compiles once and reuses.

Each helper exists for a Web API a page and a worker both expose, and each one
runs in the realm's own V8 context, so a realm owns exactly one
:class:`RealmScripts`.  It lives in ``foundation`` rather than in the platform
layer because the worker packages need it too and may not import ``platform``.
"""

from __future__ import annotations

import v8n

from .errors import as_js_error

#: JavaScript helpers compiled once per realm.  A source is evaluated rather
#: than compiled and held because the realm is the only thing that can hold a
#: compiled script, and evaluating a source that is already a function only
#: rebuilds that function -- which is why the evaluated results are cached.
_SOURCES = {
    "make_promise": (
        "(() => { let resolve, reject;"
        " const promise = new Promise((yes, no) => { resolve = yes; reject = no; });"
        " return { promise, resolve, reject }; })()"
    ),
    "error_factory": "(name, message) => new (globalThis[name] || Error)(message)",
    "dom_error_factory": "(message, name) => new DOMException(message, name)",
    "promise_resolve": "(value) => Promise.resolve(value)",
    # ``queueMicrotask`` has no helper: the embedder enqueues on V8's own
    # microtask queue directly, see enqueue_microtask.
}

#: How a Python error names its JavaScript counterpart, for the ones whose
#: Python class name is not already the JavaScript one.
_ERROR_NAMES = {
    "V8TypeError": "TypeError",
    "V8RangeError": "RangeError",
    "V8ReferenceError": "ReferenceError",
    "V8SyntaxError": "SyntaxError",
    "V8URIError": "URIError",
    "V8EvalError": "EvalError",
}


class RealmScripts:
    """Compiled JavaScript helpers bound to one V8 realm."""

    def __init__(self, realm):
        self._realm = realm
        self._helpers = None

    def clear(self):
        """Drop the helpers when the V8 realm goes away."""
        self._helpers = None

    def close(self):
        self.clear()
        self._realm = None

    def _helper(self, name):
        """The *evaluated* helper for *name*, reused across calls.

        Evaluating a source that is already a function only rebuilds that
        function, so the result is kept: ``queueMicrotask`` and promise
        resolution run once per call, where re-evaluating the source costs more
        than the call itself.  ``make_promise`` is deliberately not here -- its
        source constructs a promise, so every evaluation must be its own.
        """
        helpers = self._helpers
        if helpers is None:
            helpers = self._helpers = {}
        helper = helpers.get(name)
        if helper is None:
            helper = helpers[name] = self._realm.evaluate(_SOURCES[name])
        return helper

    def make_promise(self):
        record = self._realm.evaluate(_SOURCES["make_promise"])
        # The record is a handle, not a dict: the boundary hands an object back
        # unexpanded, so each member is read by name.
        return record.get("promise"), record.get("resolve"), record.get("reject")

    def js_error_value(self, exc):
        """Materialize a rejected Promise reason as a native JS error object.

        A reason that is already a JavaScript value -- which is what a
        rejection raised inside JavaScript arrives as -- is handed back
        unchanged, so identity survives and a handler sees the error the page
        actually threw.  A Python exception has no JavaScript counterpart, so
        one is built for it: a real ``Error`` subclass is what makes ``error
        instanceof TypeError`` true in a handler.
        """
        if isinstance(exc, v8n.Value):
            return exc
        error = as_js_error(exc)
        name = getattr(error, "_name", None)
        if not name:
            name = _ERROR_NAMES.get(type(error).__name__, "Error")
        message = str(error)
        # DOMException's name is stored on the Python instance, while native
        # Error subclasses use the constructor name.  Keep DOMException
        # construction separate so its name/code remain observable.
        if hasattr(error, "_name"):
            return self._helper("dom_error_factory")(message, name)
        return self._helper("error_factory")(name, message)

    def promise_resolve(self, value):
        return self._helper("promise_resolve")(value)

    def install_error_hooks(self, on_uncaught, on_rejection):
        """Route the failures V8 handles on its own back into the realm.

        Everything the embedder calls it catches itself -- the call it made
        returns as a Python exception.  Microtask jobs and promise reactions
        never come back to a Python frame, and a rejection is not an exception
        at all, so V8 reports both through per-realm callbacks and this installs
        them.  Doing it here is also what makes it free per call: wrapping each
        enqueued function in JavaScript would cost on every one.

        Both hooks receive one argument: the value V8 captured, through the
        same three-state boundary everything else crosses by.  That is why a
        rejection arrives as whatever was rejected -- a handle, a primitive or
        the original Python object -- rather than as a copy or a message.
        """
        self._realm.set_error_hooks(on_uncaught=on_uncaught, on_rejection=on_rejection)

    def enqueue_microtask(self, callback):
        """Queue *callback* on V8's own microtask queue.

        That is the queue promise reactions land on, which is what gives
        ``queueMicrotask`` the single FIFO order the HTML specification
        requires -- the callback is appended at the moment it is accepted, and
        V8 runs the queue when a JavaScript call returns to the embedder, the
        checkpoint the specification places after every task.

        Going straight to the queue also keeps it out of reach of the page: a
        script that replaces ``Promise`` or wraps ``Promise.prototype.then``
        cannot break, reorder or duplicate its own microtasks.

        The extension must be compiled with ``V8_CPPGC_MICROTASK_QUEUE``, which
        is what makes its view of ``MicrotaskQueue`` match the linked library's
        -- see ``v8_build.cppgc_microtask_queue_enabled``.  Without it the call
        raises instead of dispatching through a mismatched vtable.
        """
        return self._realm.enqueue_microtask(callback)


__all__ = ["RealmScripts"]
