import threading
import subprocess
import sys
from pyv8c import pyv8

from pyv8c import Browser
from pyv8c.foundation.tasks import TaskSource


def test_same_agent_source_preserves_enqueue_order_across_documents():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<iframe></iframe>')
        child = page.document.fn_querySelector('iframe')._child_services._page_realm.page
        log, done = [], threading.Event()

        def enqueue():
            page.tasks.post(lambda: log.append('parent-first'), TaskSource.POSTED_MESSAGE)
            child.tasks.post(lambda: log.append('child'), TaskSource.POSTED_MESSAGE)
            page.tasks.post(lambda: (log.append('parent-last'), done.set()), TaskSource.POSTED_MESSAGE)

        page.executor.call(enqueue)
        assert done.wait(3)
        assert log == ['parent-first', 'child', 'parent-last']
    finally:
        browser.close()


def test_stop_between_operations_prevents_new_background_execution():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        group = page.execution_group
        group.executor.call(group.request_stop)
        assert group.stopping
        assert not page.tasks.post(lambda: None)
    finally:
        browser.close()


def test_stop_during_a_task_does_not_silently_skip_nested_host_initialization():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        done = threading.Event()
        results = []
        def active_task():
            page.execution_group.request_stop()
            results.append(page.executor.call(lambda: 'initialized'))
            done.set()
        page.tasks.post(active_task)
        assert done.wait(3)
        assert results == ['initialized']
    finally:
        browser.close()


def test_background_error_listener_and_microtasks_share_the_task_budget():
    program = r"""
from pyv8c import Browser
from loguru import logger
logger.disable('pyv8c')
browser=Browser(real_network=False,stub_logging='off')
try:
    page=browser.new_profile().new_page()
    page.execution_group.executor.default_timeout=0.03
    page.evaluate("globalThis.finished=false;globalThis.errorSeen=false;onerror=()=>{errorSeen=true;queueMicrotask(function spin(){queueMicrotask(spin)});return true}")
    def bad_task():page.v8_context.exec_js("throw new Error('background failure')")
    page.tasks.post(bad_task)
    page.tasks.post(lambda:page.v8_context.exec_js('finished=true'))
    page.wait_for('finished',timeout=2)
    assert page.evaluate('errorSeen') is True
    assert page.evaluate('42',timeout=1)==42
finally:
    # The 30ms budget is for the background task, not native heap teardown.
    if 'page' in locals():page.execution_group.executor.default_timeout=2
    browser.close()
"""
    result = subprocess.run([sys.executable, '-c', program], capture_output=True,
                            text=True, timeout=8)
    assert result.returncode == 0, result.stdout + result.stderr


def test_posted_host_errors_are_reported_to_their_own_window():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<iframe></iframe>')
        child = page.document.fn_querySelector('iframe')._child_services._page_realm.page
        page.evaluate("globalThis.errors=[];onerror=message=>{errors.push(message);return true};"
                      "frames[0].eval(\"globalThis.errors=[];onerror=message=>{errors.push(message);return true}\")")
        def fail(message):raise pyv8.JsError(message)
        page.executor.post(fail, 'parent failure')
        child.executor.post(fail, 'child failure')
        page.wait_for('errors.length === 1 && frames[0].errors.length === 1', timeout=0.5)
        assert page.evaluate('[errors,frames[0].errors]') == [['parent failure'], ['child failure']]
    finally:
        browser.close()


def test_timer_submitted_from_another_thread_wakes_a_parked_agent(monkeypatch):
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        driver = page.execution_group.executor
        parked, done = threading.Event(), threading.Event()
        original_wait = driver._wake.wait
        def wait(timeout=None):
            parked.set()
            return original_wait(timeout)
        monkeypatch.setattr(driver._wake, 'wait', wait)
        driver.wakeup()
        assert parked.wait(1)
        page.tasks.schedule(done.set, 0.01)
        assert done.wait(1)
    finally:
        browser.close()
