import threading
import time

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c import Browser
from pyv8c.foundation.errors import RealmBusyError
from pyv8c.foundation.executor import RealmExecutor


def test_executor_runs_calls_on_one_owner_thread_and_closes_waiters():
    executor = RealmExecutor(name="test-realm")
    try:
        caller = threading.get_ident()
        owner = executor.call(threading.get_ident)
        assert owner != caller
        assert executor.call(lambda: executor.is_owner_thread()) is True
    finally:
        executor.close()
    with pytest.raises(RuntimeError, match="closed"):
        executor.call(lambda: None)


def test_page_background_timer_runs_without_manual_queue_pump():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        page.evaluate("window.__executorTimer = 0; setTimeout(() => __executorTimer = 1, 10)")
        time.sleep(0.2)
        assert page.evaluate("__executorTimer") == 1
    finally:
        browser.close()


def test_page_close_stops_realm_executor():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.close()
    assert page.realm.executor.closed
    browser.close()


def test_timed_out_queued_command_is_not_executed_later():
    executor = RealmExecutor(name="test-cancel")
    entered = threading.Event()
    release = threading.Event()
    side_effect = []
    try:
        executor.post(lambda: (entered.set(), release.wait(2)))
        assert entered.wait(1)
        with pytest.raises(RealmBusyError, match="command cancelled"):
            executor.call(lambda: side_effect.append("ghost"), timeout=0.01)
        release.set()
        time.sleep(0.05)
        assert side_effect == []
    finally:
        release.set()
        executor.close()
