import pytest
import pyv8
from pyv8.trace import _MAX_DISPLAY, _display


class Window:
    __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)


@pytest.fixture
def context():
    ctx = pyv8.Context(Window())
    yield ctx
    ctx.close()


def test_preview_does_not_enumerate_or_read_objects(context):
    value = context.exec_js('''globalThis.reads = 0;
        new Proxy({get x(){ reads++; return 1; }}, {
            ownKeys(){ reads++; throw Error('enumerated'); },
            get(){ reads++; throw Error('read'); }
        })''')
    assert _display(value).endswith('...')
    assert context.exec_js('reads') == 0
    context.close()
    assert _display(value).endswith('...')


def test_function_preview_ignores_page_to_string(context):
    value = context.exec_js('''globalThis.reads=0;
        const f=function original() {}; f.toString=()=>{reads++; throw Error('called');}; f''')
    assert _display(value).startswith('function original(')
    assert context.exec_js('reads') == 0


def test_long_function_and_strings_are_bounded(context):
    value = context.exec_js('(function long(){' + '/*x*/' * (_MAX_DISPLAY // 5 + 1) + '})')
    assert len(_display(value)) <= _MAX_DISPLAY + 3
    assert _display(value).endswith('...')
    assert len(_display('\n'*1000000)) <= _MAX_DISPLAY + 3
    assert '\n' not in _display('\n'*1000000)


def test_tuple_depth_and_item_budget():
    value = (1,)
    for _ in range(2000):
        value = (value,)
    assert len(_display(value)) <= _MAX_DISPLAY + 3
    assert len(_display((1,)*100000)) <= _MAX_DISPLAY + 3
    assert _display((1, 2)) == '(1, 2)'


def test_named_property_non_masking_is_an_explicit_contract():
    assert pyv8.IdlSpec().named_properties_non_masking is False
    spec=pyv8.IdlSpec(named_properties_non_masking=True)
    assert spec.named_properties_non_masking is True
    with pytest.raises(AttributeError):
        spec.named_properties_non_masking=False
