"""A realm a navigation left behind: still readable, nothing new built inside it.

Chromium models the same state as a detached global object -- its context "is still
alive, and author script may have references to the context", while its per-context
data is cut off so that it supports "less things" (``bindings/core/v8/window_proxy.h``,
``v8_dom_wrapper.cc``).  What this covers is a failure raised inside such a realm: it
has to reach the caller as an error, and it must not build the realm's own error class
there, because that work runs the embedding language again inside a realm whose document
is gone -- the reads it brings end in the checks that keep the realm reachable at all.
"""
import unittest

import pyv8


class Window:
    def get_window(self):
        return self


class Crosses(pyv8.JsError):
    """A failure that asks the realm to show a name of its own."""

    __v8_error_shape__ = ("SecurityError", (), ())


class WindowThatRaises(Window):
    def get_boom(self):
        raise Crosses("raised where the document is gone")


class TestDetachedRealm(unittest.TestCase):
    def test_detach_marks_the_realm_and_keeps_it_readable(self):
        context = pyv8.Context(Window(), ctx_type="Top")
        try:
            self.assertFalse(context.detached)
            context.detach()
            context.detach()  # idempotent: a realm is left behind once
            self.assertTrue(context.detached)
            # Left behind is not closed: what a page still holds goes on answering.
            self.assertEqual(context.exec_js("1 + 1"), 2)
        finally:
            context.close()

    def test_a_failure_raised_in_a_detached_realm_reaches_the_caller(self):
        context = pyv8.Context(Window(), ctx_type="Top")

        def boom():
            raise Crosses("raised where the document is gone")

        try:
            context.expose(boom=boom)
            # What the realm offers under the name the failure asks for.  A realm with a
            # document gets an error built from it; one a navigation left behind does not
            # build anything, so the message rides a plain error instead.
            context.exec_js(
                "function SecurityError(message) { this.message = message; }"
                "SecurityError.prototype = Object.create(Error.prototype);"
                "SecurityError.prototype.name = 'SecurityError';")
            for left_behind in (False, True):
                if left_behind:
                    context.detach()
                seen = context.exec_js(
                    "(function () { try { boom(); return 'no throw'; }"
                    " catch (error) { return error.name + '|' + error.message; } })()")
                self.assertEqual(
                    seen,
                    ("Error|" if left_behind else "SecurityError|") + "raised where the document is gone")
            # The realm is still whole once the failure has crossed.
            self.assertEqual(context.exec_js("1 + 1"), 2)
        finally:
            context.close()


if __name__ == "__main__":
    unittest.main()
