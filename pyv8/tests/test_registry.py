"""What one realm knows about each interface name, and how it learns it.

A realm keeps one entry per name: the template it built and the spec that built
it, or nothing at all.  The factory is asked about a name the realm has neither a
spec nor a template for, once; its answer may carry the specs for the whole name
chain it needs.  Because the specs arrive before the build, "a config committed
after installation" is not a state a realm can be in.
"""
from tests.values import snapshot

import unittest

from pyv8c import pyv8
from pyv8c.pyv8 import IdlSpec, MethodSpec
from pyv8c.pyv8.flag import FlagConstructor


class Window:
    __v8_idl_spec__ = IdlSpec(global_this=True)


def callback(name, value):
    def invoke(receiver):
        return value

    invoke.__name__ = name
    return invoke


def widget_class(declared_method=None):
    class Widget:
        __v8_idl_spec__ = IdlSpec(
            has_constructor=1,
            constructor_behavior=FlagConstructor.kNew,
            to_string_tag="Widget",
            methods=declared_method or (),
        )

        def __init__(self, label="default", **kwargs):
            self.label = label

        def get_label(self):
            return self.label

    return Widget


class TestRegistry(unittest.TestCase):
    def test_a_lazy_interface_read_from_another_realm_belongs_to_its_holder(self):
        class OpenWindow:
            __v8_idl_spec__ = IdlSpec(
                global_this=True, cross_origin_check=lambda self, accessor: True)

        widget = widget_class()
        asked = []

        def factory(name):
            asked.append(name)
            return widget if name == "Widget" else None

        parent = pyv8.Context(OpenWindow(), lazy_factory=lambda name: widget if name == "Widget" else None)
        child = pyv8.Context(OpenWindow(), lazy_factory=factory)
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        parent.install(["Widget"])
        child.install(["Widget"])
        parent.expose(child=child.glob)
        self.assertTrue(parent.exec_js("child.Widget !== Widget"))
        self.assertEqual(asked.count("Widget"), 1)
        parent.expose(child_constructor=child.exec_js("Widget"))
        self.assertTrue(parent.exec_js("child.Widget === child_constructor"))
        self.assertTrue(parent.exec_js(
            "Object.getPrototypeOf(child.Widget) === child.Function.prototype"))

    def make_context(self, factory):
        context = pyv8.Context(Window(), ctx_type="Top", lazy_factory=factory)
        self.addCleanup(context.close)
        return context

    def test_the_realm_asks_about_its_own_global_class_first(self):
        """The global object is the one name a realm is built around."""
        asked = []

        context = self.make_context(lambda name: asked.append(name) or None)
        self.assertEqual(asked, ["Window"])
        self.assertEqual(context.exec_js("1 + 1"), 2)

    def test_an_answer_carries_the_specs_the_name_needs(self):
        widget = widget_class()
        asked = []

        def factory(name):
            asked.append(name)
            if name != "Widget":
                return None
            overlay = widget.__v8_idl_spec__.replace(
                methods=(MethodSpec(name="marker", callback=callback("fn_marker", "wired")),),
            )
            return widget, {"Widget": overlay}

        context = self.make_context(factory)
        context.install(["Widget"])

        # The overlay landed before the template was built, so the very access
        # that materialised the name sees it.
        self.assertEqual(context.exec_js("new Widget('x').marker()"), "wired")
        self.assertEqual(asked, ["Window", "Widget"])

    def test_a_bulk_answer_covers_the_whole_chain(self):
        """One answer may carry the specs for every ancestor the build walks."""
        class Base:
            __v8_idl_spec__ = IdlSpec()

        class Leaf(Base):
            __v8_idl_spec__ = IdlSpec(has_constructor=1, constructor_behavior=FlagConstructor.kNew)

            def __init__(self, label="default", **kwargs):
                self.label = label

        asked = []

        def factory(name):
            asked.append(name)
            if name != "Leaf":
                return None
            return Leaf, {
                "Leaf": Leaf.__v8_idl_spec__,
                "Base": IdlSpec(
                    methods=(MethodSpec(name="marker", callback=callback("fn_marker", "from-base")),),
                ),
            }

        context = self.make_context(factory)
        context.expose(Leaf)

        self.assertEqual(asked, ["Window", "Leaf"])
        # Base's template came from the answer: its own spec declares no members.
        self.assertEqual(context.exec_js("new Leaf().marker()"), "from-base")

    def test_a_spec_for_an_already_built_name_is_ignored(self):
        """A template is built once, from the spec that arrived with its build."""
        widget = widget_class()

        class Host:
            __v8_idl_spec__ = IdlSpec(has_constructor=1, constructor_behavior=FlagConstructor.kNew)

        def factory(name):
            if name == "Host":
                return Host, {
                    "Host": Host.__v8_idl_spec__,
                    "Widget": widget.__v8_idl_spec__.replace(
                        methods=(MethodSpec(name="marker", callback=callback("fn_marker", "late")),),
                    ),
                }
            if name == "Widget":
                return widget
            return None

        context = self.make_context(factory)
        context.install(["Widget"])
        self.assertEqual(context.exec_js("typeof Widget"), "function")
        self.assertTrue(context.exec_js("typeof Widget.prototype.marker === 'undefined'"))

        # Materialising Host hands over a spec for the name Widget already answers
        # under; the built template keeps the spec it was built from.
        context.expose(host=Host())
        self.assertTrue(context.exec_js("typeof Widget.prototype.marker === 'undefined'"))

    def test_a_reentrant_answer_builds_instead_of_asking_again(self):
        """Asking is a state, so a build started inside an answer is not asked about."""
        widget = widget_class()
        asked = []
        holder = {}

        def factory(name):
            if name != "Widget":
                return None
            asked.append(name)
            # Converting an object of the very name being resolved must not ask
            # again: the realm is already resolving it.
            holder["context"].expose(inner=widget())
            return widget

        context = self.make_context(factory)
        holder["context"] = context
        context.install(["Widget"])

        self.assertEqual(context.exec_js("typeof Widget"), "function")
        # The object converted inside the answer shares the template the name
        # answers with, so there is one template and one ask.
        self.assertTrue(context.exec_js("inner instanceof Widget"))
        self.assertEqual(asked, ["Widget"])

    def test_a_refused_name_leaves_nothing_behind(self):
        """A name the realm refuses answers undefined and does not ask again."""
        asked = []

        def factory(name):
            asked.append(name)
            return None

        context = self.make_context(factory)
        context.install(["Missing"])

        for _ in range(2):
            self.assertEqual(context.exec_js("typeof Missing"), "undefined")
            self.assertTrue(context.exec_js("'Missing' in globalThis"))
        self.assertEqual(asked, ["Window", "Missing"])

    def test_a_failed_ask_is_asked_again(self):
        """A factory that raises is not an answer, so the name stays open."""
        attempts = []

        def factory(name):
            if name != "Widget":
                return None
            attempts.append(name)
            raise RuntimeError("nope")

        context = self.make_context(factory)
        context.install(["Widget"])

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                context.exec_js("typeof Widget")
        self.assertEqual(attempts, ["Widget", "Widget"])

    def test_an_answer_that_is_not_a_class_is_rejected(self):
        for answer in (42, [object()], (object(), {}, object())):
            with self.subTest(answer=answer):
                context = self.make_context(lambda name: answer if name == "Widget" else None)
                context.install(["Widget"])
                with self.assertRaises(TypeError):
                    context.exec_js("typeof Widget")

    def test_one_name_answers_for_one_class(self):
        """Two classes under one name would make the registry ambiguous."""
        context = self.make_context(lambda name: None)
        first = type("Widget", (), {})
        second = type("Widget", (), {})

        context.expose(first)
        with self.assertRaisesRegex(ValueError, "already registered"):
            context.expose(second)

    def test_a_template_is_per_realm(self):
        widget = widget_class()
        first = self.make_context(lambda name: widget if name == "Widget" else None)
        second = self.make_context(lambda name: None)

        first.install(["Widget"])
        second.install(["Widget"])

        self.assertEqual(first.exec_js("typeof Widget"), "function")
        self.assertEqual(second.exec_js("typeof Widget"), "undefined")


if __name__ == "__main__":
    unittest.main()
