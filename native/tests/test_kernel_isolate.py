"""Stage 1 acceptance: isolates belong to a thread, and teardown is complete.

The rule the whole kernel rests on is that an isolate is only ever entered by
the thread that created it.  These cover that rule, the handle accounting that
replaces the previous binding's hand maintained retain/release pairs, and the
refusal to shut V8 down under a live isolate.
"""

from __future__ import annotations

import gc
import threading

import pytest

import v8n


def test_isolate_opens_and_closes():
    isolate = v8n.Isolate()
    try:
        assert isolate.closed is False
        assert isolate.live_handles > 0
    finally:
        assert isolate.close() is True
    assert isolate.closed is True
    assert isolate.close() is False


def test_close_is_idempotent_and_the_object_stays_usable():
    isolate = v8n.Isolate()
    isolate.close()
    isolate.close()
    assert isolate.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        isolate.check_thread()


def test_owner_thread_is_the_creating_thread():
    isolate = v8n.Isolate()
    try:
        assert isolate.check_thread() is None
    finally:
        isolate.close()


def test_another_thread_is_rejected():
    isolate = v8n.Isolate()
    errors = []

    def intrude():
        try:
            isolate.check_thread()
        except RuntimeError as error:
            errors.append(str(error))

    try:
        thread = threading.Thread(target=intrude)
        thread.start()
        thread.join()
    finally:
        isolate.close()

    assert len(errors) == 1
    # The message has to name both threads, or a host cannot tell which one is
    # wrong.
    assert "owner thread" in errors[0]
    assert "called from" in errors[0]


def test_each_isolate_owns_the_thread_that_made_it():
    # Two threads, one isolate each: neither may touch the other's.
    results = {}

    def worker(name):
        isolate = v8n.Isolate()
        try:
            isolate.check_thread()
            results[name] = (isolate.owner_thread, isolate.closed)
        finally:
            isolate.close()
        results[f"{name}-closed"] = isolate.closed

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results["a"][1] is False
    assert results["b"][1] is False
    assert results["a"][0] != results["b"][0]
    assert results["a-closed"] is True
    assert results["b-closed"] is True


def test_handle_accounting_is_deterministic():
    # Every isolate interns the same table up front, and the slot count is the
    # single source of truth for both the handles and the interned strings.
    first = v8n.Isolate()
    second = v8n.Isolate()
    try:
        assert first.live_handles == second.live_handles
        assert first.live_handles > 0
    finally:
        first.close()
        second.close()


def test_isolate_dropped_on_a_foreign_thread_is_reclaimed():
    # A reference count reaching zero has nothing to do with the threading rule,
    # so a wrapper dropped elsewhere must not destroy V8 from that thread.  It is
    # parked and the owner finishes the job on its next isolate.
    parked = []

    def drop_without_closing():
        parked.append(v8n.Isolate())  # kept alive here, dropped on the main thread

    thread = threading.Thread(target=drop_without_closing)
    thread.start()
    thread.join()

    del parked[:]  # dealloc runs on this thread, which does not own it

    # Creating an isolate reclaims it; nothing to observe directly except that
    # the process stays healthy and the count returns to one.
    survivor = v8n.Isolate()
    try:
        assert survivor.check_thread() is None
    finally:
        survivor.close()


def test_repr_names_the_owner_thread():
    isolate = v8n.Isolate()
    try:
        assert "owner=0x" in repr(isolate)
    finally:
        isolate.close()
    assert repr(isolate) == "<v8n.Isolate closed>"


def test_shutdown_refuses_while_an_isolate_is_open():
    isolate = v8n.Isolate()
    try:
        with pytest.raises(RuntimeError, match="still open"):
            v8n.shutdown()
    finally:
        isolate.close()


def test_isolate_churn_does_not_accumulate():
    # The previous binding could only prove its teardown by reaching zero live
    # objects at the end of a long run; the kernel makes the same guarantee for
    # whole isolates, which is where a missed release is measured in megabytes.
    psutil = pytest.importorskip("psutil")
    process = psutil.Process()

    def private_megabytes():
        gc.collect()
        return process.memory_info().private / 1048576

    for _ in range(5):  # warm the allocator up before the baseline
        v8n.Isolate().close()
    baseline = private_megabytes()

    for _ in range(100):
        v8n.Isolate().close()

    growth = private_megabytes() - baseline
    # One leaked isolate is several megabytes, so this bound catches a real miss
    # while tolerating allocator noise.
    assert growth < 32, f"isolate churn grew private memory by {growth:.1f} MB"

    assert v8n.is_initialized()
    final = v8n.Isolate()
    try:
        assert final.check_thread() is None
    finally:
        final.close()
