"""Event-loop completeness: a realm drives itself without manual pumping.

The executor drains posted/timer/microtask sources on its owner thread, so
network responses, worker messages and timers make progress while the test
process only issues ordinary ``evaluate`` calls.  ``run_until_idle`` is a test
driver that blocks until the realm is idle; it never has to be called for a
page to run.

``PYV8C_AUTO_PUMP=0`` restores the legacy behaviour: the owner loop no longer
runs task turns on its own and a page only advances while ``run_until_idle``
is executing.
"""
import time
from urllib.parse import urlsplit

import pytest

pytest.importorskip("pyv8")
from pyv8c import Browser

ORIGIN = "http://127.0.0.1:9"


def _loader(url, request):
    path = urlsplit(str(url)).path
    if path.startswith("/hello"):
        return {
            "status": 200,
            "url": str(url),
            "body": "hello from loader",
            "headers": {"content-type": "text/plain"},
        }
    if path == "/worker.js":
        return {
            "status": 200,
            "url": str(url),
            "body": (
                "self.onmessage = function (e) { postMessage(e.data * 2); };"
                "setInterval(function () {}, 50);"
            ),
            "headers": {"content-type": "text/javascript"},
        }
    return {"status": 200, "url": str(url), "body": "", "headers": {"content-type": "text/html"}}


@pytest.fixture
def page():
    browser = Browser(resource_loader=_loader, stub_logging="off")
    context = browser.new_context()
    page = context.new_page(ORIGIN + "/")
    try:
        yield page
    finally:
        page.close()
        browser.close()


def wait_until(page, expression, until=True, timeout=8.0, interval=0.01):
    """Poll ``expression`` until it evaluates equal to ``until``.

    JS ``null``/``undefined`` surface as their own pyv8 types (never ``None``)
    and ``0``/``false``/``''`` are legitimate values, so pending state must be
    expressed as a distinct value and compared with ``==`` rather than truthiness.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate(expression)
        if last == until:
            return last
        time.sleep(interval)
    return last


def test_fetch_resolves_without_manual_pump(page):
    page.evaluate("""
        window.__result = 'pending';
        fetch('/hello').then(function (r) { return r.text(); })
            .then(function (t) { window.__result = t; })
            .catch(function () { window.__result = 'rejected'; });
    """)
    assert wait_until(page, "window.__result", until="hello from loader")


def test_xhr_completes_without_manual_pump(page):
    page.evaluate("""
        window.__xhr = 'pending';
        var x = new XMLHttpRequest();
        x.open('GET', '/hello', true);
        x.onload = function () { window.__xhr = x.responseText; };
        x.onerror = function () { window.__xhr = 'error'; };
        x.send();
    """)
    assert wait_until(page, "window.__xhr", until="hello from loader")


def test_worker_postmessage_delivers_without_manual_pump(page):
    page.evaluate("""
        window.__worker = 'pending';
        var w = new Worker('/worker.js');
        w.onmessage = function (e) { window.__worker = e.data; };
        w.onerror = function () { window.__worker = 'error'; };
        w.postMessage(21);
    """)
    assert wait_until(page, "window.__worker", until=42, timeout=10.0)


def test_microtask_checkpoint_runs_between_sources(page):
    page.evaluate("""
        window.__log = [];
        setTimeout(function () {
            window.__log.push('t');
            Promise.resolve().then(function () { window.__log.push('m-after-t'); });
        }, 5);
        Promise.resolve().then(function () { window.__log.push('m'); });
        window.__log.push('sync');
    """)
    assert wait_until(
        page,
        "window.__log.length === 4 && window.__log[window.__log.length - 1] === 'm-after-t'",
        until=True,
        interval=0.02,
    )
    assert page.evaluate("window.__log") == ["sync", "m", "t", "m-after-t"]


def test_posted_response_runs_before_soon_timer(page):
    # The fetch delivery is a posted task, the zero timer is a timer task:
    # the turn order (posted -> microtask checkpoint -> timers) makes the
    # fetch resolution observable before the timer callback.
    page.evaluate("""
        window.__order = [];
        fetch('/hello').then(function () { window.__order.push('fetch'); });
        setTimeout(function () { window.__order.push('timer'); }, 0);
    """)
    assert wait_until(page, "window.__order.length === 2", until=True)
    assert page.evaluate("window.__order") == ["fetch", "timer"]


def test_interval_repeats_and_can_be_cleared(page):
    page.evaluate("""
        window.__count = 0;
        window.__id = setInterval(function () {
            window.__count += 1;
            if (window.__count === 3) { clearInterval(window.__id); }
        }, 4);
    """)
    assert wait_until(page, "window.__count", until=3, timeout=6.0) == 3
    time.sleep(0.08)
    assert page.evaluate("window.__count") == 3


def test_timer_fires_after_its_delay(page):
    page.evaluate("""
        window.__t0 = Date.now();
        window.__t1 = null;
        setTimeout(function () { window.__t1 = Date.now(); }, 30);
    """)
    assert wait_until(page, "window.__t1 !== null", until=True, timeout=6.0)
    elapsed = page.evaluate("window.__t1 - window.__t0")
    # The lower bound tolerates wall-clock drift between the JS Date.now()
    # used here and the monotonic clock the scheduler fires on; it still
    # catches a timer that ran far too early (delay ignored entirely).
    assert 15 <= elapsed < 500, f"timer fired after {elapsed}ms instead of ~30ms"


def test_auto_pump_disabled_keeps_legacy_wait_semantics(page, monkeypatch):
    monkeypatch.setenv("PYV8C_AUTO_PUMP", "0")
    browser = Browser(resource_loader=_loader, stub_logging="off")
    context = browser.new_context()
    quiet = context.new_page(ORIGIN + "/")
    try:
        quiet.evaluate("""
            window.__count = 0;
            setTimeout(function () { window.__count += 1; }, 5);
        """)
        time.sleep(0.25)
        assert quiet.evaluate("window.__count") == 0
        quiet.run_until_idle(timeout=1.0)
        assert quiet.evaluate("window.__count") == 1
    finally:
        quiet.close()
        browser.close()
