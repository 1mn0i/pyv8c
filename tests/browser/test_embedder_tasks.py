import threading
import weakref
import gc

import pytest
import pyv8

from pyv8c import Browser
from pyv8c.foundation.host_errors import RealmBusyError
from pyv8c.foundation.tasks import TaskSource


def test_embedder_commands_join_source_rotation_instead_of_bypassing_queued_work():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        seen, done = [], threading.Event()
        def submit():
            page.tasks.post(lambda: seen.append('dom'), TaskSource.DOM)
            page.executor.post(lambda: (seen.append('host'), done.set()))
        page.executor.call(submit)
        assert done.wait(2)
        assert seen == ['dom', 'host']
    finally:
        browser.close()


def test_queued_timeout_cancels_work_and_releases_payload_before_owner_resumes():
    browser = Browser(real_network=False, stub_logging='off')
    gate, entered = threading.Event(), threading.Event()
    try:
        page = browser.new_profile().new_page()
        page.executor.post(lambda: (entered.set(), gate.wait(3)))
        assert entered.wait(1)
        class Callback:
            def __call__(self): raise AssertionError('cancelled command executed')
        callback = Callback()
        ref = weakref.ref(callback)
        with pytest.raises(RealmBusyError, match='cancelled'):
            page.executor.call(callback, timeout=0.02)
        del callback
        gc.collect()
        assert ref() is None
        assert not page.execution_group.executor.has_pending_for({page.executor})
        gate.set()
        assert page.evaluate('42', timeout=1) == 42
    finally:
        gate.set();browser.close()


def test_close_control_cancels_queued_host_operations_without_waiting_for_dom_rotation(monkeypatch):
    browser = Browser(real_network=False, stub_logging='off')
    gate, entered, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []
    try:
        page = browser.new_profile().new_page()
        page.executor.post(lambda: (entered.set(), gate.wait(3)))
        assert entered.wait(1)
        pending, closing = threading.Event(), threading.Event()
        driver = page.execution_group.executor
        original_submit = driver._submit_command
        def submit(command, lane):
            result = original_submit(command, lane)
            if command.owner is page.executor:
                (closing if lane is None else pending).set()
            return result
        monkeypatch.setattr(driver, '_submit_command', submit)
        def waiting():
            try:page.evaluate('globalThis.mustNotRun=true', timeout=2)
            except RuntimeError as error:errors.append(str(error))
            finally:finished.set()
        thread = threading.Thread(target=waiting)
        thread.start()
        assert pending.wait(1)
        # Queue the close through the dedicated control path before releasing
        # the active Python callback; it must overtake ordinary embedder work.
        control = threading.Thread(target=page.close)
        control.start()
        assert closing.wait(1)
        gate.set()
        assert finished.wait(3)
        thread.join(1);control.join(3)
        assert not thread.is_alive() and not control.is_alive()
        assert errors and page.closed
    finally:
        gate.set();browser.close()


def test_nested_host_dispatch_restores_the_outer_operation_deadline():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        driver = page.execution_group.executor
        def outer():
            before = driver.remaining_budget()
            page.executor.post(lambda: None)
            assert page.tasks.run_turn()
            assert driver.has_operation
            assert 0 < driver.remaining_budget() <= before
        page.executor.call(outer, timeout=1)
    finally:
        browser.close()


def test_inline_evaluate_respects_its_explicit_budget_and_does_not_extend_the_outer_one():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        def outer():
            return page.evaluate("const start=Date.now();while(Date.now()-start<200){};globalThis.overran=true", timeout=0.03)
        with pytest.raises((pyv8.JavaScriptTerminated, RealmBusyError)):
            page.executor.call(outer, timeout=1)
        assert page.evaluate('typeof overran', timeout=1) == 'undefined'
        assert page.evaluate('42', timeout=1) == 42
    finally:
        browser.close()
