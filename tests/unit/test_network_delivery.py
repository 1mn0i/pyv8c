"""Transport result handoff must release accounting even when delivery fails."""
from concurrent.futures import Future
from threading import RLock
from types import SimpleNamespace

import pytest

from pyv8c.network.backend import RequestDelivery


@pytest.mark.parametrize("outcome", ["result", "error", "submit-error", "cancel", "closed"])
@pytest.mark.parametrize("tail_error", [False, True])
def test_raw_handoff_balances_accounting(outcome, tail_error, caplog):
    future = Future()
    failure = RuntimeError("transport failed")
    counts, answers = [], []
    engine = RequestDelivery()
    engine._lock = RLock()
    engine._closed = False
    engine._call = lambda request: request

    def submit(*args):
        if outcome == "submit-error":
            raise failure
        return future

    engine.executor = SimpleNamespace(submit=submit)
    tasks = SimpleNamespace(
        begin_network=lambda: counts.append(1),
        complete_network=lambda: counts.append(-1),
        post_task=lambda *args: pytest.fail("transport queued an owner task"),
        closed=False,
    )

    def receive(value, error):
        assert counts == [1], "handoff precedes release to avoid an idle gap"
        answers.append((value, error))
        if tail_error:
            raise ValueError("handoff rejected")

    if outcome == "submit-error" and tail_error:
        with pytest.raises(ValueError, match="handoff rejected"):
            engine.request_async(tasks, object(), receive)
    else:
        handle = engine.request_async(tasks, object(), receive)
        if outcome != "submit-error":
            assert future.set_running_or_notify_cancel()
            if outcome == "cancel":
                handle.cancel()
            if outcome == "closed":
                tasks.closed = True
            if outcome == "error":
                future.set_exception(failure)
            else:
                future.set_result("ok")
    assert counts == [1, -1]
    expected = ([] if outcome in ("cancel", "closed") else
                [(None, failure)] if outcome in ("error", "submit-error") else [("ok", None)])
    assert answers == expected
    if tail_error and outcome in ("result", "error"):
        assert "handoff rejected" in caplog.text


def test_closed_engine_handoff_without_counting():
    engine = RequestDelivery()
    engine._lock = RLock()
    engine._closed = True
    answers = []
    engine.request_async(SimpleNamespace(), object(), lambda *answer: answers.append(answer))
    assert len(answers) == 1
    assert answers[0][0] is None
    assert str(answers[0][1]) == "network backend is closed"
