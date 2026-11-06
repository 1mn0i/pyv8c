"""Crossing into V8 runs without the GIL, and callbacks take it back."""

import sys
import threading
import time

from pyv8c import pyv8


class Window:
    pass


def run_javascript_for(context, milliseconds, timeout=5.0):
    return context.exec_js(
        "var t = Date.now(); while (Date.now() - t < %d) {} 'done'" % milliseconds,
        timeout=timeout,
    )


def test_the_execution_window_releases_the_gil():
    context = pyv8.Context(Window())
    previous = sys.getswitchinterval()
    # With no preemption for the length of the test, a thread that makes progress
    # while JavaScript runs can only have done so because the window gave the GIL
    # up: the interpreter itself would not have taken it away.
    sys.setswitchinterval(5.0)
    try:
        release = threading.Event()
        observed = []

        def worker():
            release.wait(2.0)
            observed.append(time.monotonic())

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.05)
        release.set()
        started = time.monotonic()
        assert run_javascript_for(context, 400) == "done"
        elapsed = time.monotonic() - started
        thread.join(2.0)
        assert observed, "the other thread never ran at all"
        assert observed[0] < started + elapsed, (
            "the other thread only ran after JavaScript finished: the window kept the GIL"
        )
    finally:
        sys.setswitchinterval(previous)
        context.close()


def test_a_callback_gets_the_gil_back():
    context = pyv8.Context(Window())
    seen = []

    def ping(value):
        seen.append(threading.get_ident())
        return value

    context.expose(ping=ping)
    try:
        assert context.exec_js("ping(7)") == 7
        assert seen == [threading.get_ident()]
    finally:
        context.close()


def test_two_isolates_run_on_two_threads_at_the_same_time():
    first = pyv8.Isolate()
    second = pyv8.Isolate()
    first_context = first.context(Window)
    second_context = second.context(Window)
    results = {}

    def run(name, context):
        results[name] = run_javascript_for(context, 300)

    try:
        threads = [
            threading.Thread(target=run, args=("first", first_context)),
            threading.Thread(target=run, args=("second", second_context)),
        ]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        elapsed = time.monotonic() - started
        assert results == {"first": "done", "second": "done"}, results
        # Serialised work would take about twice as long.
        assert elapsed < 0.55, elapsed
    finally:
        first_context.close()
        second_context.close()
        first.close()
        second.close()
