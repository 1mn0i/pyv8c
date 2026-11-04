import pytest
import pyv8


@pytest.fixture
def context():
    context = pyv8.Context(object())
    try:
        yield context
    finally:
        context.close()


def test_private_state_identity_reason_and_single_take(context):
    promise = context.exec_js('globalThis.reason={tag:1};Promise.reject(reason)')
    other = context.exec_js('Promise.reject("other")')
    state = context._promise_rejection_state
    identity = state(promise, 0)
    assert identity == state(promise, 0)
    assert identity != state(other, 0)
    assert state(promise, 2) == (False, None)
    state(promise, 1)
    found, reason = state(promise, 2)
    assert found and reason == context.exec_js('reason')
    assert state(promise, 2) == (False, None)
    context.expose(promise=promise)
    assert context.exec_js('Reflect.ownKeys(promise).length') == 0


def test_state_rejects_invalid_objects_and_foreign_realms(context):
    for value in [None, 1, context.exec_js('({})')]:
        with pytest.raises(TypeError):context._promise_rejection_state(value,0)
    pending = context.exec_js('new Promise(()=>{})')
    with pytest.raises(ValueError):context._promise_rejection_state(pending,1)
    with pytest.raises(ValueError):context._promise_rejection_state(pending,9)
    other = pyv8.Context(object())
    try:
        foreign = other.exec_js('Promise.resolve(1)')
        with pytest.raises(ValueError):context._promise_rejection_state(foreign,0)
    finally:other.close()


def test_state_close_releases_counted_private_key(context):
    promise = context.exec_js('Promise.reject("reason")')
    context._promise_rejection_state(promise,0)
    context.close()
    with pytest.raises(RuntimeError):context._promise_rejection_state(promise,0)


def test_private_key_obeys_explicit_queue_owner_and_releases_with_context():
    import threading
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    context = isolate.context(object(), microtask_queue=queue)
    other_isolate = pyv8.Isolate()
    other = other_isolate.context(object())
    try:
        promise = context.exec_js('Promise.reject("reason")')
        context._promise_rejection_state(promise,0)
        foreign = other.exec_js('Promise.reject("foreign")')
        with pytest.raises(ValueError, match='another isolate'):
            context._promise_rejection_state(foreign,0)
        errors = []
        def inspect():
            try:context._promise_rejection_state(promise,0)
            except RuntimeError as error:errors.append(error)
        thread = threading.Thread(target=inspect)
        thread.start();thread.join(1)
        assert not thread.is_alive() and len(errors) == 1
    finally:
        context.close()
        queue.close()
        assert isolate.close() is True
        other.close()
        other_isolate.close()


@pytest.mark.parametrize('freeze', ['freeze','seal','preventExtensions'])
def test_private_state_does_not_require_an_extensible_promise(context, freeze):
    promise = context.exec_js("Object." + freeze + "(Promise.reject('reason'))")
    identity = context._promise_rejection_state(promise,0)
    context._promise_rejection_state(promise,1)
    assert context._promise_rejection_state(promise,0) == identity
    assert context._promise_rejection_state(promise,2) == (True,'reason')
