"""A deadline preempts JavaScript, and the isolate survives being preempted."""

import time

import pyv8

BUSY = "var t = Date.now(); while (Date.now() - t < 5000) {}"


class Window:
    pass


def test_exec_js_timeout_terminates_and_the_isolate_survives():
    context = pyv8.Context(Window())
    try:
        started = time.monotonic()
        try:
            context.exec_js(BUSY, timeout=0.2)
            raise AssertionError("the deadline did not fire")
        except pyv8.JavaScriptTerminated:
            pass
        elapsed = time.monotonic() - started
        assert 0.1 < elapsed < 1.5, elapsed
        # CancelTerminateExecution is what makes this work; without it a timeout
        # left the isolate unusable.
        assert context.exec_js("1 + 1") == 2
    finally:
        context.close()


def test_the_context_timeout_setting_uses_the_same_deadline():
    context = pyv8.Context(Window(), timeout=0.2)
    try:
        try:
            context.exec_js(BUSY)
            raise AssertionError("the deadline did not fire")
        except pyv8.JavaScriptTerminated:
            pass
        assert context.exec_js("2 + 2") == 4
    finally:
        context.close()


def test_set_deadline_covers_a_batch_of_crossings():
    context = pyv8.Context(Window())
    try:
        context.set_deadline(0.15)
        try:
            context.exec_js(BUSY)
            raise AssertionError("the deadline did not fire")
        except pyv8.JavaScriptTerminated:
            pass
        context.clear_deadline()
        assert context.exec_js("3 + 3") == 6
    finally:
        context.close()


def test_terminate_aborts_and_the_next_crossing_resumes():
    context = pyv8.Context(Window())
    try:
        context.terminate()
        try:
            context.exec_js(BUSY)
            raise AssertionError("terminate did not abort the crossing")
        except pyv8.JavaScriptTerminated:
            pass
        assert context.exec_js("4 + 4") == 8
    finally:
        context.close()


def test_a_deadline_only_terminates_the_isolate_it_was_armed_on():
    busy_isolate = pyv8.Isolate()
    other_isolate = pyv8.Isolate()
    busy = busy_isolate.context(Window, timeout=0.05)
    other = other_isolate.context(Window)
    try:
        try:
            busy.exec_js("while (true) {}")
            raise AssertionError("the deadline did not fire")
        except pyv8.JavaScriptTerminated:
            pass
        assert other.exec_js("6 * 7") == 42
        assert busy.exec_js("20 + 22") == 42
    finally:
        busy.close()
        other.close()
        busy_isolate.close()
        other_isolate.close()
