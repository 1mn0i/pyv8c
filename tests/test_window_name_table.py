"""The window's own property table, and what an index means to it.

The binding installs this table as a *named* interceptor with
``kOnlyInterceptStrings``, so V8 reaches it with names even when the caller wrote
an index: ``window[7]`` arrives as ``"7"``, exactly like ``window["7"]``.  That is
what makes the two spellings one lookup -- and it is why a host that matches an
element by ``id`` or ``name`` finds the element whose id is ``"7"`` either way.

The indexed interceptors that used to sit beside it were therefore never called;
they rendered the index into a decimal name to reach this same table by a longer
route.  These tests pin the behaviour that made them unreachable, so that the
next person to look at this does not have to rediscover it.

The class below declares ``named_properties_object`` because the flag, not the
class name, is how the binding recognises the table; the name is kept because the
world its template is built for is still read from it.
"""

import unittest

import pyv8
from pyv8 import IdlSpec


def window_properties_class(seen):
    class WindowProperties:
        __v8_idl_spec__ = IdlSpec(
            constructor_behavior=0,
            has_constructor=0,
            to_string_tag="WindowProperties",
            named_properties_object=True,
        )

        def __v8_property_name_get__(self, name):
            seen.append(("get", name))
            return "named:" + name

        def __v8_property_name_query__(self, name):
            seen.append(("query", name))
            return 1

    return WindowProperties


class TestWindowNameTable(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.context = pyv8.Context(window_properties_class(self.seen)())

    def tearDown(self):
        self.context.close()

    def test_an_index_is_looked_up_as_its_decimal_name(self):
        self.assertEqual(self.context.exec_js("this[7]"), "named:7")
        self.assertEqual(self.seen[-1], ("get", "7"))
        self.assertIs(type(self.seen[-1][1]), str)

    def test_the_two_spellings_are_one_lookup(self):
        self.context.exec_js("this['7']")
        self.context.exec_js("this[7]")
        self.assertEqual(self.seen[-2:], [("get", "7"), ("get", "7")])

    def test_a_non_index_name_arrives_unchanged(self):
        self.assertEqual(self.context.exec_js("this['alpha']"), "named:alpha")
        self.assertEqual(self.seen[-1], ("get", "alpha"))

    def test_a_wide_index_keeps_its_digits(self):
        index = 2 ** 32 - 1
        self.context.exec_js("this[%d]" % index)
        self.assertEqual(self.seen[-1], ("get", str(index)))

    def test_the_query_handler_is_consulted_for_existence(self):
        self.context.exec_js("'probe' in this")
        self.assertIn(("query", "probe"), self.seen)

    def test_a_declined_lookup_is_undefined(self):
        class WindowProperties:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=0,
                has_constructor=0,
                to_string_tag="WindowProperties",
                named_properties_object=True,
            )

            def __v8_property_name_get__(self, name):
                return None

        context = pyv8.Context(WindowProperties())
        try:
            self.assertIs(context.exec_js("this[3] === undefined"), True)
            self.assertIs(context.exec_js("this['nope'] === undefined"), True)
        finally:
            context.close()
