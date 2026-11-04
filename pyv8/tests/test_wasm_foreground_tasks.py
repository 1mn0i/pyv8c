"""The private Context foreground-task entry point and its safety rules."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import pyv8


def test_an_idle_context_reports_no_foreground_task():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    context = isolate.context(object(), microtask_queue=queue)
    try:
        assert not hasattr(context, 'pump_platform_tasks')
        assert context._run_foreground_task() is False
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(RuntimeError, match="owner thread"):
                pool.submit(context._run_foreground_task).result()
    finally:
        context.close()
        queue.close()
        isolate.close()


def test_pumping_inside_a_context_entry_is_rejected():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    context = isolate.context(object(), microtask_queue=queue)
    try:
        context.expose(pump=context._run_foreground_task)
        with pytest.raises(RuntimeError, match="during a Context entry"):
            context.exec_js("pump()")
    finally:
        context.close()
        queue.close()
        isolate.close()


def test_closing_isolates_with_pending_wasm_foreground_tasks():
    # The platform must retire the old runner before an address can be reused
    # by a subsequent isolate, even while background compilation is pending.
    for _ in range(30):
        isolate = pyv8.Isolate()
        queue = pyv8.MicrotaskQueue(isolate)
        context = isolate.context(object(), microtask_queue=queue)
        context.exec_js("void WebAssembly.compile(new Uint8Array([0,97,115,109,1,0,0,0]))")
        context.close()
        queue.close()
        isolate.close()


def test_foreground_wait_is_released_by_isolate_close():
    isolate = pyv8.Isolate()
    version = isolate._foreground_version()
    # Ensure this isolate has a registration, even if it has no V8 tasks.
    assert isolate._foreground_deadline() is None
    version = isolate._foreground_version()
    entered = threading.Event()
    result = []

    def wait():
        entered.set()
        try:
            result.append(isolate._foreground_wait(version))
        except RuntimeError:
            # Closing may win the race before the call enters native code.
            result.append(None)

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    assert entered.wait(2)
    isolate.close()
    thread.join(2)
    assert not thread.is_alive()
    assert result and result[0] != version
