from tests.values import snapshot

"""Lazy interface declaration via Context.declare_lazy + lazy_factory.

declare_lazy installs a non-enumerable accessor on the global object for every
requested name without building a FunctionTemplate. The first property access
calls the optional lazy_factory, builds the real template and caches it, so
later accesses never re-enter Python.
"""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec
import unittest

import pyv8
from pyv8.flag import (
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

    def test_declare_lazy_returns_declared_count(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        self.assertEqual(context.declare_lazy(["Widget", "Other", "Third"]), 3)

    def test_declared_names_are_visible_but_not_enumerable(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.declare_lazy(["Widget"])

        self.assertIn("Widget", snapshot(context.exec_js("Object.getOwnPropertyNames(globalThis)")))
        # DontEnum keeps the names out of enumeration, matching real globals.
        self.assertNotIn("Widget", snapshot(context.exec_js("Object.keys(globalThis)")))

    def test_first_access_materialises_and_second_hits_cache(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.declare_lazy(["Widget"])

        self.assertEqual(context.exec_js("typeof Widget"), "function")
        self.assertEqual(self.calls, ["Widget"])

        # The template is cached after the first access.
        self.assertEqual(context.exec_js("typeof Widget"), "function")
        self.assertEqual(self.calls, ["Widget"])

    def test_materialised_template_is_fully_functional(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.declare_lazy(["Widget"])

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
        context.declare_lazy(["Missing"])

        self.assertEqual(context.exec_js("typeof Missing"), "undefined")
        self.assertEqual(self.calls, ["Missing"])

    def test_factory_exception_follows_js_error_semantics(self):
        """A failing factory must behave like any other JS-thrown error."""
        def factory(name):
            raise RuntimeError("boom " + name)

        context = pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory)
        self.addCleanup(context.close)
        context.declare_lazy(["Bad"])

        # Uncaught: surfaces to the Python caller of exec_js.
        with self.assertRaises(RuntimeError) as caught:
            context.exec_js("typeof Bad")
        self.assertIn("boom Bad", str(caught.exception))

        # Caught: JavaScript must be able to intercept it like a V8 exception.
        self.assertEqual(
            context.exec_js("try { Bad } catch (e) { e.message }"),
            "boom Bad",
        )

    def test_failed_materialisation_is_not_cached(self):
        """A name that failed to materialise must retry on the next access."""
        attempts = []

        def factory(name):
            attempts.append(name)
            raise RuntimeError("nope")

        context = pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory)
        self.addCleanup(context.close)
        context.declare_lazy(["Bad"])

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                context.exec_js("typeof Bad")
        self.assertEqual(attempts, ["Bad", "Bad"])

    def test_without_factory_names_resolve_to_undefined(self):
        """Backwards compatibility: no lazy_factory keeps the old behaviour."""
        context = pyv8.Context(Window(), ctx_type="Top")
        self.addCleanup(context.close)
        context.declare_lazy(["Widget"])

        self.assertEqual(context.exec_js("typeof Widget"), "undefined")

    def test_declare_lazy_skips_names_in_the_skip_set(self):
        """Names the caller already installed keep their concrete value."""
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        context.expose(self.widget, name="Widget")
        exposed = context.exec_js("Widget")

        self.assertEqual(context.declare_lazy(["Widget", "Other"], skip=["Widget"]), 1)
        # The eager value is untouched: still a data property, not an accessor.
        self.assertIsNotNone(context.exec_js("Object.getOwnPropertyDescriptor(globalThis, 'Widget').value"))
        self.assertEqual(context.exec_js("new Widget('x').describe()"), "widget:x")
        self.assertFalse(self.calls, "the skipped name must not reach the factory")
        self.assertIsNotNone(exposed)

    def test_skip_set_accepts_any_sequence(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        self.assertEqual(context.declare_lazy(["Widget"], skip=("Widget",)), 0)
        self.assertEqual(context.declare_lazy(["Widget"], skip=set()), 1)

    def test_declare_lazy_rejects_non_string_names(self):
        context = self.make_context({"Widget": self.widget})
        self.addCleanup(context.close)
        with self.assertRaises(TypeError):
            context.declare_lazy([1, 2])

    def test_lazy_factory_must_be_callable(self):
        with self.assertRaises(TypeError):
            pyv8.Context(Window(), ctx_type="Top", lazy_factory="not callable")

    def test_accessors_are_isolated_per_context(self):
        first = self.make_context({"Widget": self.widget})
        self.addCleanup(first.close)
        second = self.make_context({})
        self.addCleanup(second.close)
        first.declare_lazy(["Widget"])
        second.declare_lazy(["Widget"])

        self.assertEqual(first.exec_js("typeof Widget"), "function")
        self.assertEqual(second.exec_js("typeof Widget"), "undefined")


if __name__ == "__main__":
    unittest.main()
