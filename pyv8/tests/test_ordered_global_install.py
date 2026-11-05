"""Native ordered global installation retains Web IDL property behavior."""
import json

import pytest

import pyv8


class OrderedWindow:
    __v8_idl_spec__ = pyv8.IdlSpec(
        global_this=True,
        attributes=(pyv8.AttributeSpec(name="first", getter="get_first", location=0),),
        methods=(pyv8.MethodSpec(name="second", callback="fn_second", location=0),),
    )

    def get_first(self):
        return 7

    def fn_second(self):
        return 8


def test_ordered_global_interleaves_instance_members_and_constructor():
    class Widget:
        __v8_idl_spec__ = pyv8.IdlSpec(has_constructor=1)

    context = pyv8.Context(OrderedWindow(), ordered_global=True,
                           lazy_factory=lambda name: Widget if name == "Widget" else None)
    assert context.exec_js("'first' in globalThis") is False
    assert context.install_ordered([("method", "second"), ("constructor", "Widget"),
                                    ("attribute", "first")]) == 3
    result = json.loads(context.exec_js("""JSON.stringify((() => {
        const names = Object.getOwnPropertyNames(globalThis);
        const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'first');
        return [names.indexOf('second') < names.indexOf('Widget'),
                names.indexOf('Widget') < names.indexOf('first'),
                first, second(), descriptor.enumerable, typeof descriptor.get];
    })())"""))
    assert result == [True, True, 7, 8, True, "function"]


def test_ordered_global_rejects_unknown_and_duplicate_members():
    context = pyv8.Context(OrderedWindow(), ordered_global=True)
    with pytest.raises(ValueError, match="unknown global attribute"):
        context.install_ordered([("attribute", "missing")])
    context.install_ordered([("attribute", "first")])
    with pytest.raises(ValueError, match="already exists"):
        context.install_ordered([("attribute", "first")])


def test_old_template_reservation_is_rejected():
    with pytest.raises(TypeError, match="preinstall"):
        pyv8.Context(OrderedWindow(), ordered_global=True, preinstall=("first",))


def test_old_global_phase_installers_are_not_exposed():
    for name in ("install_late", "install_late_methods", "install_tail"):
        assert not hasattr(pyv8.Context, name)
    with pytest.raises(TypeError, match="late_attributes"):
        pyv8.IdlSpec(late_attributes=())


def test_repositioned_builtin_keeps_value_and_descriptor():
    context = pyv8.Context(OrderedWindow(), ordered_global=True)
    context.exec_js("let savedWasm = WebAssembly; let savedDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'WebAssembly');")
    context.install_ordered([("method", "second"), ("builtin", "WebAssembly"),
                             ("attribute", "first")])
    assert json.loads(context.exec_js("""JSON.stringify((() => {
        const names = Object.getOwnPropertyNames(globalThis);
        const d = Object.getOwnPropertyDescriptor(globalThis, 'WebAssembly');
        return [names.indexOf('second') < names.indexOf('WebAssembly'),
                names.indexOf('WebAssembly') < names.indexOf('first'),
                WebAssembly === savedWasm, d.writable === savedDescriptor.writable,
                d.enumerable === savedDescriptor.enumerable,
                d.configurable === savedDescriptor.configurable];
    })())""")) == [True, True, True, True, True, True]


def test_failed_install_restores_moved_builtins():
    context = pyv8.Context(OrderedWindow(), ordered_global=True)
    context.exec_js("let savedWasm = WebAssembly")
    before = context.exec_js("JSON.stringify(Object.getOwnPropertyNames(globalThis))")
    with pytest.raises(ValueError, match="unknown global attribute"):
        context.install_ordered([("attribute", "missing"), ("builtin", "WebAssembly")])
    assert context.exec_js("WebAssembly === savedWasm") is True
    assert context.exec_js("JSON.stringify(Object.getOwnPropertyNames(globalThis))") == before


def test_nonconfigurable_builtin_is_left_in_place():
    context = pyv8.Context(OrderedWindow(), ordered_global=True)
    context.exec_js("Object.defineProperty(globalThis, 'fixedBuiltin', {value: 42, configurable: false})")
    assert context.install_ordered([("method", "second"), ("builtin", "fixedBuiltin")]) == 2
    assert context.exec_js("fixedBuiltin") == 42
    assert context.exec_js("Object.getOwnPropertyDescriptor(globalThis, 'fixedBuiltin').configurable") is False
