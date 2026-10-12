"""A realm's window can outlive the context it was created in.

Chromium keeps one object per browsing context and hands it to the realm a
navigation creates (``local_window_proxy.cc``: the proxy is filled in once and
passed to every later ``Context::New``), which is what lets a page hold a frame's
window across navigations.  These cover that path: the object survives, its state
does not, and the Python side is registered with the realm reading it.
"""
import unittest

import pyv8


class Window:
    def get_window(self):
        return self


class TestContextReuse(unittest.TestCase):
    def test_the_same_window_answers_for_the_next_context(self):
        first = pyv8.Context(Window(), ctx_type="Top")
        first.exec_js("globalThis.__mark = 7")
        carried = first.glob

        second = pyv8.Context(Window(), ctx_type="Top", reuse_global=carried)
        try:
            second.expose(carried_window=carried)
            self.assertTrue(second.exec_js("carried_window === globalThis"))
            # The identity stays; what the last document wrote does not.
            self.assertEqual(second.exec_js("typeof globalThis.__mark"), "undefined")
        finally:
            first.close()

        # Closing the realm it came from leaves this one whole: the new context
        # owns the object now, and nothing of the old one is reached through it.
        second.exec_js("globalThis.__kept = 11")
        self.assertTrue(second.exec_js("carried_window === globalThis"))
        self.assertEqual(second.exec_js("globalThis.__kept"), 11)
        second.close()

    def test_the_python_window_is_registered_with_the_new_realm(self):
        first = pyv8.Context(Window(), ctx_type="Top")
        carried = first.glob

        window = Window()
        second = pyv8.Context(window, ctx_type="Top", reuse_global=carried)
        try:
            second.expose(owner=window)
            self.assertTrue(second.exec_js("owner === globalThis"))
        finally:
            first.close()
            second.close()

    def test_a_realm_object_that_is_not_a_window_is_refused(self):
        first = pyv8.Context(Window(), ctx_type="Top")
        try:
            with self.assertRaises(TypeError):
                pyv8.Context(Window(), ctx_type="Top", reuse_global=first.exec_js("({})"))
        finally:
            first.close()

    def test_reuse_is_refused_across_isolates(self):
        first = pyv8.Context(Window(), ctx_type="Top")
        other = pyv8.Isolate()
        try:
            with self.assertRaises(RuntimeError):
                other.context(Window(), ctx_type="Top", reuse_global=first.glob)
        finally:
            first.close()
            other.close()


if __name__ == "__main__":
    unittest.main()
