"""Context.update_configs commits class config overlays atomically.

Python-side binding sessions materialise one interface at a time after the
Context was constructed, so the overlay cannot be part of the constructor's
``class_configs`` snapshot.  ``update_configs`` merges those late overlays
into the dict the Context already owns; the lazy template builder must then
see the committed config on the very access that triggered materialisation.

The commit protocol only adds new classes, but the Context must stay coherent
even if a caller re-commits an overlay for a class whose template is already
cached (template snapshots are immutable once built).
"""
import unittest

import pyv8
from pyv8.flag import FlagConstructor


class Window:
    __v8_global_this__ = True


def operation(name, callback):
    return (name, callback, 0, 0, 1, 0, 0, 0, 0)


def make_widget_class():
    class Widget:
        __v8_has_constructor__ = 1
        __v8_constructor__ = FlagConstructor.kNew
        __v8_to_string_tag__ = "Widget"

        def __init__(self, value="base", **kwargs):
            self._value = value

        def get_value(self):
            return self._value

    return Widget


def marker_callback(value):
    """A direct callback, matching the pattern used across the native suite."""
    def fn_marker(receiver):
        return value

    fn_marker.__v8_direct_callback__ = True
    fn_marker.__name__ = "fn_marker"
    return fn_marker


def widget_overlay(widget, value, marker="marker"):
    owner = type("Owner", (), {"fn_marker": marker_callback(value)})
    return {
        widget: {
            "__v8_method__": (operation(marker, "fn_marker"),),
            "__v8_callback_owner__": owner,
        }
    }


class TestUpdateConfigs(unittest.TestCase):
    def test_update_configs_commits_overlay_for_lazy_template(self):
        """A lazy_factory that commits through update_configs is fully wired:
        the overlay is visible to the very access that triggered it."""
        widget = make_widget_class()
        committed = {}

        def factory(name, context):
            if name == "Widget":
                overlay = widget_overlay(widget, "first")
                committed[name] = context.update_configs(overlay)
                return widget
            return None

        context = pyv8.Context(Window(), ctx_type="Top",
                               lazy_factory=lambda name: factory(name, context))
        self.addCleanup(context.close)
        context.declare_lazy(["Widget"])

        self.assertEqual(context.exec_js("new Widget('x').marker()"), "first")
        self.assertEqual(committed.get("Widget"), 1)

    def test_update_configs_returns_added_count(self):
        widget = make_widget_class()
        other = type("Other", (object,), {})
        context = pyv8.Context(Window(), class_configs={widget: {}})
        self.addCleanup(context.close)

        self.assertEqual(context.update_configs({other: {}}), 1)
        # Re-committing an existing class is idempotent from the count's view.
        self.assertEqual(context.update_configs({other: {}}), 0)
        self.assertEqual(context.update_configs({}), 0)

    def test_update_configs_merges_with_constructor_snapshot(self):
        widget = make_widget_class()
        context = pyv8.Context(Window(), class_configs={widget: {}})
        self.addCleanup(context.close)

        self.assertEqual(context.update_configs(widget_overlay(widget, "late")), 0)
        context.expose(widget)
        self.assertEqual(context.exec_js("new Widget('x').marker()"), "late")

    def test_update_configs_requires_a_mapping(self):
        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        with self.assertRaises(TypeError):
            context.update_configs([1, 2, 3])
        with self.assertRaises(TypeError):
            context.update_configs("Widget")

    def test_update_configs_after_close_is_rejected(self):
        context = pyv8.Context(Window())
        context.close()
        with self.assertRaises(RuntimeError):
            context.update_configs({})

    def test_recommit_does_not_break_cached_template(self):
        """A second commit for an already-built class must not disturb the
        cached FunctionTemplate: snapshots are taken once, at build time."""
        widget = make_widget_class()
        context = pyv8.Context(Window(), class_configs=widget_overlay(widget, "v1"))
        self.addCleanup(context.close)
        context.expose(widget)
        self.assertEqual(context.exec_js("new Widget('x').marker()"), "v1")

        # Re-commit with a different behaviour; the existing template is cached
        # and must still report v1 rather than crashing or half-updating.
        context.update_configs(widget_overlay(widget, "v2"))
        self.assertEqual(context.exec_js("new Widget('x').marker()"), "v1")

    def test_late_commit_is_visible_to_a_fresh_context(self):
        """The same late overlay is honoured when a new Context builds the
        template after the commit — the dict merge itself is intact."""
        widget = make_widget_class()
        context = pyv8.Context(Window(), class_configs={})
        self.addCleanup(context.close)
        context.update_configs(widget_overlay(widget, "v2"))
        context.expose(widget)
        self.assertEqual(context.exec_js("new Widget('x').marker()"), "v2")


if __name__ == "__main__":
    unittest.main()
