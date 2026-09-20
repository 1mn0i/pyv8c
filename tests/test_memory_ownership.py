import gc
import weakref
import subprocess
import sys

import pytest
import pyv8


class Window:
    pass


def collect():
    for _ in range(3):
        pyv8._pyv8.low_memory_notification()
        gc.collect()


def test_closed_context_releases_callbacks():
    refs = []
    for _ in range(20):
        context = pyv8.Context(Window())
        callback = lambda: 42
        refs.append(weakref.ref(callback))
        context.expose(callback=callback)
        assert context.exec_js('callback()') == 42
        context.close()
        del context, callback
    collect()
    assert not any(ref() is not None for ref in refs)


def test_global_context_cycle_is_collectable():
    window = Window()
    ref = weakref.ref(window)
    context = pyv8.Context(window)
    window.context = context
    del window, context
    collect()
    assert ref() is None


def test_callback_context_cycle_is_collectable():
    def create():
        context = pyv8.Context(Window())
        callback = lambda: context.exec_js('42')
        context.expose(callback=callback)
        return weakref.ref(callback)
    ref = create()
    collect()
    assert ref() is None


def test_close_invalidates_retained_handles_and_preserves_other_context():
    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    obj = first.exec_js('new (class Payload {})()')
    function = first.exec_js('() => 42')
    typed = first.exec_js('new Uint8Array(4)')
    promise = first.exec_js('Promise.resolve(42)')
    script = pyv8.Script(first, '42')
    first.close()
    first.close()
    try:
        for operation in (lambda: obj['x'], function, lambda: repr(typed),
                          lambda: repr(promise), lambda: second.exec_js(script)):
            with pytest.raises(RuntimeError):
                operation()
        assert second.exec_js('21 * 2') == 42
    finally:
        second.close()


def test_foreign_host_callback_after_close_throws_without_crashing():
    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    first.expose(callback=lambda: 42)
    second.expose(foreign=first.exec_js('callback'))
    first.close()
    try:
        assert second.exec_js("try { foreign(); false } catch (e) { true }")
    finally:
        second.close()


def test_close_from_callback_is_deferred_safely():
    context = pyv8.Context(Window())
    context.expose(close=context.close)
    context.exec_js('close()')
    with pytest.raises(RuntimeError):
        context.exec_js('42')


def test_error_hooks_are_context_owned():
    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    observed = []
    callback = lambda *args: observed.append('first')
    ref = weakref.ref(callback)
    first.set_error_hooks(None, callback)
    second.set_error_hooks(None, lambda *args: observed.append('second'))
    first.exec_js('Promise.reject(1)')
    second.exec_js('Promise.reject(2)')
    assert observed == ['first', 'second']
    first.close()
    del callback, first
    collect()
    assert ref() is None
    second.exec_js('Promise.reject(3)')
    assert observed == ['first', 'second', 'second']
    second.close()


def test_gc_and_isolate_close_without_testing_flag():
    isolate = pyv8.Isolate()
    context = isolate.context(Window())
    context.gc()
    isolate.gc()
    with pytest.raises(RuntimeError, match='persistent handle'):
        isolate.close()
    context.close()
    assert isolate.close()
    pyv8.v8_gc()


def test_function_identity_and_other_context_survive_close():
    callback = lambda: 42
    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    first.expose(a=callback, b=callback)
    second.expose(callback=callback)
    assert first.exec_js('a === b')
    first.close()
    assert second.exec_js('callback()') == 42
    second.close()


def test_foreign_reference_keeps_source_context_alive_until_close():
    def create(target):
        window = Window()
        source = pyv8.Context(window)
        window.context = source
        source.expose(callback=lambda: 42)
        target.expose(foreign=source.exec_js('callback'))
        return weakref.ref(window)
    target = pyv8.Context(Window())
    ref = create(target)
    collect()
    assert ref() is not None
    assert target.exec_js('foreign()') == 42
    target.close()
    collect()
    assert ref() is None


def test_explicit_close_removes_incoming_context_dependencies():
    class Context(pyv8.Context):
        pass

    target = pyv8.Context(Window())
    source = Context(Window())
    ref = weakref.ref(source)
    source.expose(callback=lambda: 42)
    target.expose(foreign=source.exec_js('callback'))
    source.close()
    del source
    collect()
    assert ref() is None
    assert target.exec_js('try { foreign(); false } catch (e) { true }')
    target.close()


def test_wrapped_javascript_cycle_is_collectable():
    window = Window()
    ref = weakref.ref(window)
    context = pyv8.Context(window)
    window.function = context.exec_js('() => 42')
    del window, context
    collect()
    assert ref() is None


def test_native_allocations_release_heap_type_reference():
    collect()
    context_type = pyv8.Context
    baseline = sys.getrefcount(context_type)
    for _ in range(50):
        context = context_type(Window())
        context.close()
        del context
    collect()
    assert sys.getrefcount(context_type) == baseline


def test_context_attribute_access_releases_temporary_global_wrapper():
    context = pyv8.Context(Window())
    wrapper = context.glob
    baseline = sys.getrefcount(wrapper)
    for _ in range(100):
        context.value = 42
        assert context.value == 42
    assert sys.getrefcount(wrapper) == baseline
    context.close()


def test_promise_rejection_uses_creation_context():
    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    observed = []
    first.set_error_hooks(None, lambda *args: observed.append(('first', args[0])))
    second.set_error_hooks(None, lambda *args: observed.append(('second', args[0])))
    promise = first.exec_js('Promise.reject(42)')
    second.expose(promise=promise)
    second.exec_js('promise.catch(() => {})')
    assert observed == [('first', 'unhandledrejection'), ('first', 'rejectionhandled')]
    first.close()
    second.close()


def test_foreign_class_bindings_are_invalidated():
    class Payload:
        __v8_idl_spec__ = pyv8.IdlSpec(
            attributes=(pyv8.AttributeSpec(name='value', getter='get_value'),),
            methods=(pyv8.MethodSpec(name='method', callback='method'),),
        )

        def get_value(self):
            return 42

        def method(self):
            return 42

    first = pyv8.Context(Window())
    second = pyv8.Context(Window())
    first.expose(Payload=Payload, payload=Payload())
    second.expose(constructor=first.exec_js('Payload'),
                  method=first.exec_js('payload.method.bind(payload)'),
                  getter=first.exec_js("Object.getOwnPropertyDescriptor(Payload.prototype, 'value').get.bind(payload)"))
    assert second.exec_js('[method(), getter()]').to_python() == [42, 42]
    first.close()
    for expression in ('new constructor()', 'method()', 'getter()'):
        assert second.exec_js(f'try {{ {expression}; false }} catch (e) {{ true }}')
    second.close()


@pytest.mark.parametrize('source', [
    """
isolate = pyv8.Isolate()
c = isolate.context(Window())
del isolate
c.close()
del c
gc.collect()
pyv8.shutdown_v8()
""",
    """
isolate = pyv8.Isolate()
c = isolate.context(Window())
c.expose(close=c.close)
c.exec_js('close()')
assert isolate.close()
""",
    """
import threading
c = pyv8.Context(Window())
errors = []
def run():
    for _ in range(100):
        try: c.exec_js('42')
        except RuntimeError: return
thread = threading.Thread(target=run)
thread.start()
c.close()
thread.join(5)
assert not thread.is_alive()
""",
])
def test_shutdown_and_close_in_subprocess(source):
    result = subprocess.run(
        [sys.executable, '-X', 'faulthandler', '-c',
         'import pyv8, gc\nclass Window: pass\n' + source],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_host_object_pairs_do_not_accumulate_or_alias_over_a_long_run():
    """Python 对象与 JS 对象的配对表必须随对象一起收缩。

    长时间运行的程序会不停创建新的宿主对象。每一轮都必须:新对象拿到新的 JS
    对象(留下旧条目的表会把上一个 JS 对象交回来,WeakSet 立刻能认出来),并且
    一旦双方都放掉,配对条目就要消失——条目持有着对 Python 对象的强引用,留下
    来就等于把宿主对象永久钉在内存里。
    """
    context = pyv8.Context(Window())
    try:
        class Payload:
            pass

        context.exec_js("globalThis.seen = new WeakSet()")
        refs = []
        for _ in range(300):
            payload = Payload()
            refs.append(weakref.ref(payload))
            context.expose(payload=payload)
            assert context.exec_js("seen.has(payload) === false")
            context.exec_js("seen.add(payload); payload = null")
            del payload
        collect()
        assert [ref for ref in refs if ref() is not None] == []
    finally:
        context.close()


def test_host_object_identity_survives_unhashable_and_slotted_types():
    """不可哈希或不可创建弱引用的宿主对象也只能有一个 JS 对象。

    身份表按对象地址索引,不把对象当作字典键,也不要求它可被弱引用;这两种宿主
    类以前拿不到缓存,每次跨边界都会长出第二个 JS 对象。
    """
    context = pyv8.Context(Window())
    try:
        class Unhashable:
            __hash__ = None

        class Slotted:
            __slots__ = ()

        for cls in (Unhashable, Slotted):
            instance = cls()
            context.expose(first=instance, second=instance)
            assert context.exec_js("first === second") is True
            context.exec_js("first = second = null")
            del instance
    finally:
        context.close()


def test_close_releases_host_object_pairs_and_their_handles():
    """关闭 Context 必须放掉配对表里的每一对,包括已经交给 Python 的句柄。"""

    class Payload:
        pass

    context = pyv8.Context(Window())
    payload = Payload()
    ref = weakref.ref(payload)
    context.expose(payload=payload)
    handle = context.js_obj(payload)
    assert handle is not None
    context.close()
    with pytest.raises(RuntimeError):
        handle.marker
    del handle, payload
    collect()
    assert ref() is None
