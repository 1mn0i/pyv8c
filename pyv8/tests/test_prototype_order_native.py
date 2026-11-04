"""Native prototype ordering preserves the descriptors and realm identity."""
import json
import pytest
import pyv8


class Window:
    __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)


class Example:
    __v8_idl_spec__ = pyv8.IdlSpec(
        has_constructor=1,
        attributes=(pyv8.AttributeSpec(name='early', getter='get_early'),
                    pyv8.AttributeSpec(name='late', getter='get_late')),
        methods=(pyv8.MethodSpec(name='method', callback='method'),),
        prototype_late_members=('constructor', 'late'),
    )

    def get_early(self): return 1
    def get_late(self): return 2
    def method(self): return 3


def test_spec_validates_late_names():
    assert pyv8.IdlSpec(prototype_late_members=('x',)).prototype_late_members == ('x',)
    for invalid in (['x'], (1,), ('x\0y',), 'x'):
        with pytest.raises(TypeError):
            pyv8.IdlSpec(prototype_late_members=invalid)


def test_instance_first_orders_prototype_without_changing_descriptor():
    context = pyv8.Context(Window())
    try:
        context.expose(example=Example())
        result = context.exec_js('''JSON.stringify((() => {
            const p = Object.getPrototypeOf(example);
            const d = Object.getOwnPropertyDescriptor(p, 'late');
            return [Object.getOwnPropertyNames(p), example.late, example.method(),
                    typeof d.get, d.enumerable, d.configurable];
        })())''')
        assert json.loads(result) == [['early', 'method', 'constructor', 'late'], 2, 3, 'function', True, True]
    finally:
        context.close()
