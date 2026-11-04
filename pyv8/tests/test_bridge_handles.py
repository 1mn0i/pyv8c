import gc
import pytest
import pyv8


@pytest.fixture
def context():
    value = pyv8.Context(object())
    yield value
    value.close()


def test_exec_and_call_return_handles(context):
    value = context.exec_js("({items: [1, 2]})")
    assert isinstance(value, pyv8.JSObject)
    assert value.to_python() == {"items": [1, 2]}
    function = context.exec_js("() => [3, 4]")
    assert function().to_python() == [3, 4]


def test_default_conversion_does_not_observe_proxy(context):
    seen = []
    context.expose(capture=lambda value: seen.append(value) or value)
    assert context.exec_js("""(() => {
        const proxy = new Proxy({}, {get(){throw 1}, ownKeys(){throw 2},
                                    getPrototypeOf(){throw 3}});
        return capture(proxy) === proxy;
    })()""") is True
    assert isinstance(seen[0], pyv8.JSObject)
    assert isinstance(seen[0].to_python(), pyv8.JSObject)
    with pytest.raises(pyv8.JSException):
        seen[0]._get("value")


def test_explicit_conversion_preserves_cycles_and_aliases(context):
    value = context.exec_js("(() => { const a={}; a.self=a; return [a,a] })()")
    data = value.to_python()
    assert data[0] is data[1]
    assert data[0]["self"] is data[0]


@pytest.mark.parametrize("count", [0, 1, 4, 8, 9, 16, 64])
def test_vectorcall_stack_and_heap_arguments(context, count):
    calls = []
    context.expose(record=lambda *args: calls.append(args) or len(args))
    assert context.exec_js(f"record(...Array({count}).fill(7))") == count
    assert calls == [(7,) * count]


def test_callback_close_keeps_inflight_arguments_alive(context):
    def close(value):
        context.close()
        gc.collect()
        return 9
    context.expose(close=close)
    assert context.exec_js("close({})") == 9
    with pytest.raises(RuntimeError):
        context.exec_js("1")


def test_removed_conversion_switch(context):
    with pytest.raises(TypeError):
        pyv8.Context(object(), preserve_arguments=True)
    value = context.exec_js("({})")
    with pytest.raises(AttributeError):
        value._to_python


def test_exception_state_is_invalidated_with_context():
    isolate = pyv8.Isolate()
    context = isolate.context(object())
    with pytest.raises(pyv8.JSException) as captured:
        context.exec_js("throw {code: 7}")
    assert captured.value.original.to_python() == {"code": 7}
    context.close()
    with pytest.raises(RuntimeError):
        captured.value.original
    isolate.close()


def test_exception_context_cycle_is_collectable():
    import weakref

    class Window:
        pass
    window = Window()
    reference = weakref.ref(window)
    context = pyv8.Context(window)
    try:
        context.exec_js("throw Error('cycle')")
    except pyv8.JSException as error:
        window.error = error
    del window, context
    for _ in range(3):
        gc.collect()
        pyv8.low_memory_notification()
    assert reference() is None
