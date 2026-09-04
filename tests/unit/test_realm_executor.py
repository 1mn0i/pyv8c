import threading
import time

import pytest

from pyv8c import Browser
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
