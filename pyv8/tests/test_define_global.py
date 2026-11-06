"""Context.define_global installs one value with the attributes the caller names."""
from pyv8c import pyv8

from pyv8c.pyv8.flag import V8Attribute


def make_context():
    class Window:
        __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)

    return pyv8.Context(Window(), ctx_type="Top")


def test_define_global_installs_a_non_enumerable_property():
    context = make_context()
    try:
        context.exec_js("function URL() {}")
        context.define_global("webkitURL", context.glob["URL"], attributes=V8Attribute.DontEnum)

        assert context.exec_js("globalThis.webkitURL === globalThis.URL") is True
        assert context.exec_js("Object.keys(globalThis).includes('webkitURL')") is False
        assert context.exec_js("Object.getOwnPropertyNames(globalThis).includes('webkitURL')") is True
    finally:
        context.close()


def test_define_global_is_enumerable_unless_asked_otherwise():
    context = make_context()
    try:
        context.define_global("plain", 1)
        assert context.exec_js("plain") == 1
        assert context.exec_js("Object.keys(globalThis).includes('plain')") is True
    finally:
        context.close()
