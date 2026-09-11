"""Promise callback delivery, settlement and chaining across the Python bridge."""

import pytest
import pyv8


@pytest.fixture
def context():
    owner = pyv8.Context(object())
    try:
        yield owner
    finally:
        owner.close()


def test_fulfilled_promise_delivers_callback_once(context):
    observed = []
    promise = context.exec_js("Promise.resolve('success')")
    assert isinstance(promise, pyv8.JSPromise)
    promise.then(observed.append)
    context.exec_js("0")
    assert observed == ["success"]
    context.exec_js("0")
    assert observed == ["success"]


def test_rejected_promise_selects_rejection_callback(context):
    resolved, rejected = [], []
    promise = context.exec_js("Promise.reject('failure')")
    promise.then(resolved.append, rejected.append)
    context.exec_js("0")
    assert resolved == []
    assert rejected == ["failure"]


def test_pending_promise_settles_once(context):
    observed = []
    promise = context.exec_js("new Promise(resolve => { globalThis.finish = resolve; })")
    promise.then(observed.append)
    context.exec_js("0")
    assert observed == []
    context.exec_js("finish(42); finish(99)")
    assert observed == [42]


def test_python_callback_result_flows_to_chained_promise(context):
    observed = []
    promise = context.exec_js("Promise.resolve(40)")
    chained = promise.then(lambda value: value + 2)
    assert isinstance(chained, pyv8.JSPromise)
    chained.then(observed.append)
    context.exec_js("0")
    assert observed == [42]


def test_python_callback_error_rejects_chained_promise(context):
    class Failure(Exception):
        __v8_error__ = True
        __v8_error_name__ = "TypeError"

    def fail(value):
        raise Failure("callback failed")

    observed = []
    promise = context.exec_js("Promise.resolve(40)")
    chained = promise.then(fail)
    chained.then(None, lambda error: observed.append((error.name, error.message)))
    context.exec_js("0")
    assert observed == [("TypeError", "callback failed")]
    assert context.exec_js("6 * 7") == 42
