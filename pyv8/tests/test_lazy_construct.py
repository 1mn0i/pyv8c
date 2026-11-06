from tests.values import snapshot

"""Lazy interface declaration via Context.install + lazy_factory.

install declares a non-enumerable accessor on the global object for every
requested name without building a FunctionTemplate. The first property access
calls the optional lazy_factory, builds the real template and caches it, so
later accesses never re-enter Python.
"""
from pyv8c.pyv8 import IdlSpec, AttributeSpec, MethodSpec
import unittest

from pyv8c import pyv8
from pyv8c.pyv8.flag import (
    FlagConstructor,
    FlagLocation,
    FlagReceiverCheck,
    FlagCrossOriginCheck,
    SideEffectType,
    V8Attribute,
)


class Window:
    __v8_idl_spec__ = IdlSpec(
        global_this=True,
    )


def make_widget_class():
    class Widget:
        __v8_idl_spec__ = IdlSpec(
            has_constructor=1,
            constructor_behavior=FlagConstructor.kNew,
            to_string_tag="Widget",
            methods=(
                MethodSpec(
                    name="describe",
                    callback="fn_describe",
                    length=0,
                    location=FlagLocation.kPrototype,
                    property_flags=0,
                    receiver_check=FlagReceiverCheck.kCheck,
                    cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
                    side_effect=SideEffectType.kHasSideEffect,
                ),
            ),
            attributes=(
                AttributeSpec(
                    name="label",
                    getter="get_label",
                    setter=None,
                    location=FlagLocation.kInstance,
                    property_flags=V8Attribute.ReadOnly,
                    receiver_check=FlagReceiverCheck.kCheck,
                    cross_origin_get_check=FlagCrossOriginCheck.kDoNotCheck,
                    cross_origin_set_check=FlagCrossOriginCheck.kDoNotCheck,
                    side_effect=SideEffectType.kHasNoSideEffect,
                ),
            ),
        )

        def __init__(self, label="default", **kwargs):
            # The native bridge passes the owning window as a keyword argument.
            self.label = label

        def fn_describe(self):
            return "widget:" + self.label

        def get_label(self):
            return self.label

    return Widget


class TestLazyConstruct(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.widget = make_widget_class()

    def make_context(self, mapping=None, factory=None, **kwargs):
        if factory is None:
            def factory(name):
                self.calls.append(name)
                return (mapping or {}).get(name)
        return pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory, **kwargs)

    def test_install_returns_declared_count(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        self.assertEqual(context.install(["Widget", "Other", "Third"]), 3)

    def test_declared_names_are_visible_but_not_enumerable(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.install(["Widget"])

        self.assertIn("Widget", snapshot(context.exec_js("Object.getOwnPropertyNames(globalThis)")))
        # DontEnum keeps the names out of enumeration, matching real globals.
        self.assertNotIn("Widget", snapshot(context.exec_js("Object.keys(globalThis)")))

    def test_first_access_materialises_and_second_hits_cache(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.install(["Widget"])

        # The realm's own global class is the first thing it is asked about.
        self.assertEqual(self.calls, ["Window"])

        self.assertEqual(context.exec_js("typeof Widget"), "function")
        self.assertEqual(self.calls, ["Window", "Widget"])

        # The template is cached after the first access.
        self.assertEqual(context.exec_js("typeof Widget"), "function")
        self.assertEqual(self.calls, ["Window", "Widget"])

    def test_materialised_template_is_fully_functional(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.install(["Widget"])

        self.assertEqual(
            context.exec_js("Object.prototype.toString.call(Widget.prototype)"),
            "[object Widget]",
        )
        self.assertEqual(context.exec_js("new Widget('hi').describe()"), "widget:hi")
        self.assertEqual(context.exec_js("new Widget('hi').label"), "hi")
        self.assertTrue(context.exec_js("new Widget() instanceof Widget"))

    def test_factory_returning_none_yields_undefined(self):
        context = self.make_context({})
        self.addCleanup(context.close)
        context.install(["Missing"])

        self.assertEqual(context.exec_js("typeof Missing"), "undefined")
        self.assertEqual(self.calls, ["Window", "Missing"])

        # The refusal is remembered: a later access does not ask again.
        self.assertEqual(context.exec_js("typeof Missing"), "undefined")
        self.assertEqual(self.calls, ["Window", "Missing"])

    def test_factory_exception_is_a_host_failure(self):
        """A failing factory is the host's failure: the caller hears it, JavaScript does not."""
        def factory(name):
            if name != "Bad":
                return None
            raise RuntimeError("boom " + name)

        context = pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory)
        self.addCleanup(context.close)
        context.install(["Bad"])

        # Uncaught: surfaces to the Python caller of exec_js.
        with self.assertRaises(RuntimeError) as caught:
            context.exec_js("typeof Bad")
        self.assertIn("boom Bad", str(caught.exception))

        # A script cannot catch what it was never shown: the crossing ends there, and
        # the reason is the one the factory raised.
        with self.assertRaises(RuntimeError) as caught:
            context.exec_js("try { Bad } catch (e) { e.message }")
        self.assertIn("boom Bad", str(caught.exception))
        self.assertEqual(context.exec_js("6 * 7"), 42)

    def test_failed_materialisation_is_not_cached(self):
        """A name that failed to materialise must retry on the next access."""
        attempts = []

        def factory(name):
            if name != "Bad":
                return None
            attempts.append(name)
            raise RuntimeError("nope")

        context = pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory)
        self.addCleanup(context.close)
        context.install(["Bad"])

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                context.exec_js("typeof Bad")
        self.assertEqual(attempts, ["Bad", "Bad"])

    def test_without_factory_names_resolve_to_undefined(self):
        """Without a lazy factory, an installed name remains undefined."""
        context = pyv8.Context(Window(), ctx_type="Top")
        self.addCleanup(context.close)
        context.install(["Widget"])

        self.assertEqual(context.exec_js("typeof Widget"), "undefined")

    def test_install_rejects_non_string_names(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        with self.assertRaises(TypeError):
            context.install([1, 2])

    def test_lazy_factory_must_be_callable(self):
        with self.assertRaises(TypeError):
            pyv8.Context(Window(), ctx_type="Top", lazy_factory="not callable")

    def test_accessors_are_isolated_per_context(self):
        first = self.make_context({"Widget": self.widget})
        self.addCleanup(first.close)
        second = self.make_context({})
        self.addCleanup(second.close)
        first.install(["Widget"])
        second.install(["Widget"])

        self.assertEqual(first.exec_js("typeof Widget"), "function")
        self.assertEqual(second.exec_js("typeof Widget"), "undefined")


if __name__ == "__main__":
    unittest.main()
