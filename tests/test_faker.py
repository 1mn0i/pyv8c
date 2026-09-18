"""Native Faker helpers, not the higher-level Browser Web API surface."""

import pytest
import pyv8


@pytest.fixture
def context():
    class Window:
        __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)

    owner = pyv8.Context(Window())
    try:
        yield owner
    finally:
        owner.close()


@pytest.mark.parametrize("expression, check", [
    ("611n", "copy === source"),
    ("Object(611n)", "copy !== source && copy.valueOf() === source.valueOf()"),
    ("/example/gi", "copy !== source && copy.source === source.source && copy.flags === source.flags"),
    ("({nested: {value: 1}})", "copy !== source && copy.nested !== source.nested && copy.nested.value === 1"),
])
def test_structured_clone_values(context, expression, check):
    assert context.exec_js(f"""(() => {{
        const source = {expression};
        const copy = faker.structuredClone(source);
        return {check};
    }})()""") is True


def test_structured_clone_preserves_cycles_and_aliases(context):
    assert context.exec_js("""(() => {
        const child = {value: 42};
        const source = {a: child, b: child};
        source.self = source;
        const copy = faker.structuredClone(source);
        copy.a.value = 7;
        return copy !== source && copy.self === copy && copy.a === copy.b
            && copy.a !== child && child.value === 42 && copy.b.value === 7;
    })()""") is True


def test_structured_clone_copies_buffers_and_map_keys(context):
    assert context.exec_js("""(() => {
        const key = {id: 1};
        const data = new Uint8Array([1, 2, 3]);
        const copy = faker.structuredClone({key, data, map: new Map([[key, data]])});
        copy.data[0] = 9;
        return copy.map.get(copy.key) === copy.data && copy.key !== key
            && copy.data.buffer !== data.buffer && data[0] === 1
            && copy.data[0] === 9 && copy.data[2] === 3;
    })()""") is True


@pytest.mark.parametrize("expression", ["(() => {})", "Symbol('value')", "new WeakMap()"])
def test_structured_clone_rejects_uncloneable_values(context, expression):
    # The native helper currently reports TypeError, not Browser DOMException.
    assert context.exec_js(f"""(() => {{
        try {{ faker.structuredClone({expression}); }}
        catch (error) {{ return error instanceof TypeError && /could not be cloned/.test(error.message); }}
        return false;
    }})()""") is True
    assert context.exec_js("faker.structuredClone({answer: 42}).answer") == 42


def test_structured_clone_requires_an_argument_but_accepts_undefined(context):
    assert context.exec_js("""(() => {
        try { faker.structuredClone(); }
        catch (error) { return error instanceof TypeError && faker.structuredClone(undefined) === undefined; }
        return false;
    })()""") is True


def test_callbacks_forward_more_than_ten_arguments(context):
    assert context.exec_js("""
        const args = Array.from({length: 32}, (_, index) => index);
        const documentAll = faker.new_document_all(function () { return Array.from(arguments); });
        const nativeWrapper = faker.native_wrapper({cb: function () { return Array.from(arguments); }});
        [documentAll(...args), nativeWrapper(...args)];
    """).to_python() == [list(range(32)), list(range(32))]


def test_run_js_requires_string_source(context):
    assert context.exec_js("""(() => {
        try { faker.run_js(1, 'test.js'); }
        catch (error) { return error instanceof TypeError; }
        return false;
    })()""") is True
