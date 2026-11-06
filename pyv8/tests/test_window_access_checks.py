"""Native access-check callbacks, denial, exception forwarding and teardown."""
from pyv8c.pyv8 import IdlSpec, AttributeSpec

import gc
import weakref

from pyv8c import pyv8


class DOMException(pyv8.JsError):
    """A failure the accessing realm is told about, built with that realm's constructor.

    Its realm constructor is not one of the standard names, so the class says what it is
    shown as and what travels with it.
    """

    __v8_error_shape__ = ("DOMException", ("_name",), (("_code", "code"),))

    def __init__(self):
        super().__init__("cross-origin access denied")
        self._name = "SecurityError"
        self._code = 18


class CheckedWindow:
    __v8_idl_spec__ = IdlSpec(
        cross_origin_check="__v8_cross_origin_check__",
        global_this=True,
        attributes=(
            AttributeSpec(
                name="secret",
                getter="get_secret",
                setter=None,
                location=0,
                property_flags=0,
                receiver_check=0,
                cross_origin_get_check=0,
                cross_origin_set_check=0,
                side_effect=0,
            ),
        ),
    )

    def __init__(self, origin, *, throws=False):
        self.origin = origin
        self.throws = throws
        self.calls = []

    def get_secret(self):
        return 42

    def __v8_cross_origin_check__(self, accessor):
        self.calls.append(accessor.origin)
        if self.origin == accessor.origin:
            return True
        if self.throws:
            raise DOMException()
        return False


def test_access_check_denial_and_same_origin_contexts():
    first = CheckedWindow("a")
    second = CheckedWindow("b")
    one = pyv8.Context(first)
    two = pyv8.Context(second)
    try:
        one.expose(foreign=two.js_obj(second))
        assert one.exec_js('try { foreign.secret; } catch (e) { e.name; }') == 'TypeError'
        assert second.calls == ['a']
        second.origin = 'a'
        assert one.exec_js('foreign.secret') == 42
        assert second.calls[-1] == 'a'
        assert two.exec_js('secret') == 42
    finally:
        one.close()
        two.close()


def test_python_dom_exception_survives_native_access_check():
    first = CheckedWindow('a')
    second = CheckedWindow('b', throws=True)
    one = pyv8.Context(first)
    two = pyv8.Context(second)
    try:
        # Raw V8 has no DOM. A browser embedding provides its DOMException
        # constructor; the class says what it is shown as, so the error is built by the
        # accessing realm's own.
        one.exec_js('''globalThis.DOMException = class DOMException extends Error {
            constructor(message, name) { super(message); this.name = name; this.code = 18; }
        }''')
        one.expose(foreign=two.js_obj(second))
        assert one.exec_js('''try { foreign.secret; } catch(e) {
            [e.name, e.code, e instanceof DOMException, e.message];
        }''').to_python() == ['SecurityError', 18, True, 'cross-origin access denied']
        assert one.exec_js('40 + 2') == 42
    finally:
        one.close()
        two.close()


def test_multiple_checked_contexts_release_python_globals():
    references = []
    for i in range(12):
        first = CheckedWindow('a')
        second = CheckedWindow('b')
        references.extend([weakref.ref(first), weakref.ref(second)])
        one = pyv8.Context(first)
        two = pyv8.Context(second)
        one.expose(foreign=two.js_obj(second))
        assert one.exec_js('try { foreign.secret; } catch(e) { e.name; }') == 'TypeError'
        one.close()
        two.close()
        del first, second, one, two
    # Python globals remain in weak V8 wrappers until a V8 GC cycle, not
    # merely until Python's cyclic collector runs.
    probe = pyv8.Context(CheckedWindow('gc-probe'))
    probe.gc()
    probe.close()
    del probe
    gc.collect()
    assert all(ref() is None for ref in references)


def test_native_backed_object_conversion_does_not_probe_guarded_properties():
    class Guarded:
        __v8_idl_spec__ = IdlSpec(
            name_query="__v8_name_query__",
        )
        def __v8_name_query__(self, name):
            raise RuntimeError('property inspection is forbidden')

    context = pyv8.Context(CheckedWindow('a'))
    target = Guarded()
    try:
        context.expose(target=target, identity=lambda value: value)
        assert context.exec_js('identity(target) === target') is True
    finally:
        context.close()
