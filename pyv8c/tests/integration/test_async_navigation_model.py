from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import pyv8

from pyv8c import Browser
from pyv8c.foundation.host_errors import HostStateError, RealmBusyError


def test_script_navigation_does_not_queue_behind_entire_dom_backlog(monkeypatch):
    browser = Browser(real_network=False, stub_logging='off',
                      resource_loader=lambda url, request: '<body>next</body>')
    reached = threading.Event()
    dom = []
    observed = []
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body>first</body>', url='http://site.test/')
        original = page.navigation._load

        def load(*args, **kwargs):
            observed.append(len(dom))
            reached.set()
            return original(*args, **kwargs)

        monkeypatch.setattr(page.navigation, '_load', load)

        def enqueue():
            for number in range(100):
                page.tasks.post(lambda number=number: dom.append(number))
            page.navigation.request_navigation('/next')

        page.executor.call(enqueue)
        assert reached.wait(3)
        assert observed[0] < 100, 'navigation was put behind the entire DOM source backlog'
        page.wait_for("document.body.textContent === 'next'", timeout=3)
        assert page.evaluate('location.pathname') == '/next'
    finally:
        browser.close()


def test_slow_main_document_does_not_block_evaluate_or_close():
    entered, release = threading.Event(), threading.Event()
    def loader(url, options):
        entered.set()
        assert release.wait(3)
        return {'body': '<body>late</body>'}
    browser = Browser(real_network=False, resource_loader=loader, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        with ThreadPoolExecutor(1) as pool:
            navigation = pool.submit(page.goto, 'https://slow.test/', timeout=2)
            try:
                assert entered.wait(1)
                assert page.evaluate('1+1', timeout=0.5) == 2
                page.close()
                with pytest.raises(HostStateError, match='closed'):
                    navigation.result(1)
            finally:
                release.set()
    finally:
        release.set()
        browser.close()


def test_waiting_for_load_tracks_the_document_and_releases_timeout_waiter():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.wait_for_load_state(timeout=0.5)
        page.executor.call(setattr, page.lifecycle, 'document_load_fired', False)
        with pytest.raises(TimeoutError):
            page.wait_for_load_state(timeout=0.02)
        assert not page.lifecycle._state_waiters
        future = page.executor.call(page.lifecycle.wait_for_state, 'load')
        page.set_content('<body>new</body>')
        with pytest.raises(HostStateError, match='replaced'):
            future.result(1)
    finally:
        browser.close()


def test_evaluate_budget_terminates_microtasks_without_terminating_next_operation():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        with pytest.raises((pyv8.JavaScriptTerminated, RealmBusyError)):
            page.evaluate('function again(){Promise.resolve().then(again)};again()', timeout=0.03)
        assert page.evaluate('1+1', timeout=1) == 2
    finally:
        browser.close()


def test_script_and_callback_cleanup_happen_only_with_an_empty_js_stack():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('''<script>globalThis.log=[];
          Promise.resolve().then(()=>log.push(document.currentScript && document.currentScript.tagName));
          log.push('first');</script><script>log.push('second')</script>''')
        assert page.evaluate('log') == ['first', 'SCRIPT', 'second']
        assert page.evaluate('''log=[];let target=new EventTarget();
          target.addEventListener('x',()=>{log.push('one');queueMicrotask(()=>log.push('micro'))});
          target.addEventListener('x',()=>log.push('two'));
          target.dispatchEvent(new Event('x'));log.push('after');'ok' ''') == 'ok'
        assert page.evaluate('log') == ['one', 'two', 'after', 'micro']
    finally:
        browser.close()


def test_close_terminates_only_its_execution_group_and_does_not_run_queued_jobs():
    entered = threading.Event()
    browser = Browser(real_network=False, stub_logging='off')
    try:
        profile = browser.new_profile()
        page, other = profile.new_page(), profile.new_page()
        page.executor.call(page.v8_context.expose, entered=entered.set)
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(page.evaluate, '''entered();
                function again(){Promise.resolve().then(again)}; again();while(true){}''', timeout=3)
            assert entered.wait(1)
            page.close()
            with pytest.raises((pyv8.JavaScriptTerminated, RealmBusyError)):
                running.result(1)
        assert other.evaluate('1+1') == 2
    finally:
        browser.close()


def test_idle_ignores_future_timers_and_times_out_on_self_posting_work():
    browser = Browser(real_network=False, stub_logging='off')
    stop = threading.Event()
    try:
        page = browser.new_profile().new_page()
        page.evaluate('setTimeout(()=>{},60000)')
        assert page.run_until_idle(0)
        entered = threading.Event()
        def recurring():
            entered.set()
            if not stop.is_set():
                page.tasks.post(recurring)
        page.tasks.post(recurring)
        assert entered.wait(1)
        assert page.run_until_idle(0.02) is False
        stop.set()
        assert page.run_until_idle(1)
    finally:
        stop.set()
        browser.close()
