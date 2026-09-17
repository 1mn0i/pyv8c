"""The JavaScript helpers one realm compiles once and reuses.

Each helper exists for a Web API a page and a worker both expose, and each one
runs in the realm's own V8 context, so a realm owns exactly one
:class:`RealmScripts`.  It lives in ``foundation`` rather than in the platform
layer because the worker packages need it too and may not import ``platform``.
"""

from __future__ import annotations

import pyv8

from .errors import as_js_error

#: JavaScript helpers compiled once per realm.
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


class RealmScripts:
    """Compiled JavaScript helpers bound to one V8 context."""

    def __init__(self, context):
        self._context = context
        self._cache = None
        self._helpers = None

    def clear(self):
        """Drop the compiled scripts when the V8 context goes away."""
        self._cache = None
        self._helpers = None

    @property
    def _scripts(self):
        """Compile the realm's JavaScript helpers exactly once.

        ``exec_js`` builds a fresh ``Script`` for every source string, and that
        construction costs several times more than evaluating an already
        compiled one. Fetch, XHR and every rejected Promise go through these
        helpers, so they are compiled once per realm and re-evaluated after.
        """
        scripts = self._cache
        if scripts is None:
            scripts = {
                key: pyv8.Script(self._context, source)
                for key, source in _SOURCES.items()
            }
            self._cache = scripts
        return scripts

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
            helper = helpers[name] = self._context.exec_js(self._scripts[name])
        return helper

    def make_promise(self):
        record = self._context.exec_js(self._scripts["make_promise"])
        return record["promise"], record["resolve"], record["reject"]

    def js_error_value(self, exc):
        """Materialize a rejected Promise reason as a native JS error object."""
        if isinstance(exc, pyv8.JSException):
            return exc.original
        error = as_js_error(exc)
        name = getattr(error, "_name", None)
        if not name:
            name = {
                "V8TypeError": "TypeError", "V8RangeError": "RangeError",
                "V8ReferenceError": "ReferenceError", "V8SyntaxError": "SyntaxError",
                "V8URIError": "URIError", "V8EvalError": "EvalError",
            }.get(type(error).__name__, "Error")
        message = str(error)
        # DOMException's name is stored on the Python instance, while native
        # Error subclasses use the constructor name.  Keep DOMException
        # construction separate so its name/code remain observable.
        if hasattr(error, "_name"):
            return self._helper("dom_error_factory").call(None, message, name)
        return self._helper("error_factory").call(None, name, message)

    def promise_resolve(self, value):
        return self._helper("promise_resolve").call(None, value)

    def install_error_hooks(self, on_uncaught, on_rejection):
        """Route the failures V8 handles on its own back into the realm.

        Everything the embedder calls it catches itself -- the call it made
        returns as a Python exception.  Microtask jobs and promise reactions
        never come back to a Python frame, and a rejection is not an exception
        at all, so V8 reports both through isolate-wide callbacks and this
        installs them.  Doing it here is also what makes it free per call:
        wrapping each enqueued function in JavaScript would cost on every one.

        ``on_uncaught`` receives ``(message, source, line, column, error)`` and
        ``on_rejection`` receives ``(event, identity, promise, reason)``; either
        may be ``None`` to leave that report alone.  Both run on the realm's own
        thread while it is inside JavaScript.
        """
        self._context.set_error_hooks(on_uncaught, on_rejection)

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
        return self._context.enqueue_microtask(callback)


__all__ = ["RealmScripts"]
