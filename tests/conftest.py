"""Shared fixtures for the pyv8c suite.

This module must stay importable *without* the native :mod:`pyv8` extension:
``tests/unit`` is expected to run in environments that only have the Chromium
metadata tooling.  Every runtime fixture therefore performs its import lazily
and skips through :func:`pytest.importorskip` when the extension is missing.

Nothing here is mandatory.  A test module that already declares a local
``browser``/``page`` fixture keeps its own definition -- the closer fixture
wins -- so adopting these helpers is incremental rather than all-or-nothing.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

import pytest

ORIGIN = "http://127.0.0.1:9"

#: Options every fixture-backed browser shares.  Stub logging floods the
#: capture buffer and makes assertion output unreadable, so it stays off
#: unless a test explicitly asks for it.
QUIET_BROWSER_OPTIONS = {"stub_logging": "off"}


def require_pyv8():
    """Skip the calling test when the native extension is not importable."""
    return pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")


def default_loader(url, request):
    """Offline loader used by fixtures that do not care about responses.

    Serves an empty 200 document for every URL so a page can navigate and
    reach ``load`` without touching the network.
    """
    path = urlsplit(str(url)).path
    if path.endswith(".js"):
        return {
            "status": 200,
            "url": str(url),
            "body": "",
            "headers": {"content-type": "text/javascript"},
        }
    return {"status": 200, "url": str(url), "body": "", "headers": {"content-type": "text/html"}}


def make_browser(**options):
    """Build a quiet, offline :class:`~pyv8c.Browser`.

    ``resource_loader`` defaults to :func:`default_loader`; pass an explicit
    one to serve fixtures.  Unknown keywords are forwarded to ``Browser``.
    """
    require_pyv8()
    from pyv8c import Browser

    options = {**QUIET_BROWSER_OPTIONS, **options}
    options.setdefault("resource_loader", default_loader)
    return Browser(**options)


@pytest.fixture
def browser_factory():
    """Return :func:`make_browser`, letting a test supply its own options."""
    return make_browser


@pytest.fixture
def browser():
    """A quiet offline Browser, closed on teardown."""
    instance = make_browser()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def page(browser):
    """A fresh Page on ``about:blank``, closed before its Browser."""
    instance = browser.new_context().new_page()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def thread_leak_check():
    """Assert the process returns to its baseline thread count.

    Yields a callable ``check(tolerance=1, timeout=10.0)``.  Threads started by
    a realm executor, the DevTools server or the network pool are daemonised
    and may take a moment to unwind, so the check polls until the deadline
    instead of sampling once.
    """
    baseline = threading.active_count()

    def check(tolerance=1, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and threading.active_count() > baseline + tolerance:
            time.sleep(0.05)
        leaked = threading.active_count() - baseline
        assert leaked <= tolerance, (
            f"{leaked} threads leaked (baseline {baseline}, tolerance {tolerance}); "
            f"live: {sorted(t.name for t in threading.enumerate())}"
        )
        return leaked

    yield check


def wait_until(page, expression, until=True, timeout=8.0, interval=0.01):
    """Poll ``expression`` until it evaluates equal to ``until``.

    JS ``null``/``undefined`` surface as their own pyv8 types (never ``None``)
    and ``0``/``false``/``''`` are legitimate values, so pending state must be
    expressed as a distinct value and compared with ``==`` rather than
    truthiness.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate(expression)
        if last == until:
            return last
        time.sleep(interval)
    return last


def run_threads(threads, timeout=20):
    """Start and join *threads*, asserting none of them wedges."""
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(t.is_alive() for t in threads), "one or more threads wedged"
