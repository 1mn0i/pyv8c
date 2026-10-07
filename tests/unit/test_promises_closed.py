"""A realm whose document has been replaced must not raise at the page.

``PageRealm.close_context`` sets ``self.context = None`` when the document is replaced, and work
queued by the old document is still delivered afterwards -- the commit path cancels only timers.
Settling a promise in that state used to reach a JavaScript helper that no longer existed and
raised ``AttributeError: 'NoneType' object has no attribute 'exec_js'``; the page received that
engine failure as its own, and Cloudflare's widget stored it and reported it (measured
2026-10-06; two of the four raised calls in that run were this one defect).

``foundation.promises`` is where that policy lives now, and a context of ``None`` is exactly the
state a replaced document leaves behind -- so the rule can be asked without V8 at all.
"""
import pytest

pytest.importorskip("pyv8", reason="the module is built against the native extension")

from pyv8c.foundation import promises  # noqa: E402


def test_a_realm_without_a_context_answers_instead_of_raising():
    #: Both readings match what callers already do for "there is no realm to ask".
    assert promises.promise_resolve(None, "value") == "value"
    assert promises.promise_resolve(None, None) is None

    promise, resolve, reject = promises.make_promise(None)
    assert promise is None
    resolve("ignored")  # nobody can observe a promise in a document that is gone
    reject(RuntimeError("ignored"))

    assert promises.enqueue_microtask(None, lambda: None) is False


def test_a_rejection_reason_crosses_as_itself():
    #: The native resolver turns a Python exception into a JavaScript error when it settles,
    #: so nothing is built here -- and a non-exception reason stays what it is.
    error = RuntimeError("late rejection")
    assert promises.js_error_value(error) is error
    assert promises.js_error_value("plain reason") == "plain reason"
