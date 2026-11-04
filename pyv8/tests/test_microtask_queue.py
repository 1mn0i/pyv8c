"""Explicit agent queues: execution never drains a neighbour's jobs."""
from concurrent.futures import ThreadPoolExecutor

import pytest
import pyv8


def test_explicit_queues_are_isolated_and_shared_contexts_keep_fifo():
    isolate = pyv8.Isolate()
    a, b = pyv8.MicrotaskQueue(isolate), pyv8.MicrotaskQueue(isolate)
    contexts = [isolate.context(object(), microtask_queue=q) for q in (a, a, b)]
    seen = []
    try:
        for index, ctx in enumerate(contexts):
            ctx.exec_js(f"globalThis.log=[]; Promise.resolve().then(()=>log.push({index}))")
            ctx.enqueue_microtask(lambda index=index: seen.append(index))
        assert all(ctx.exec_js("log.length") == 0 for ctx in contexts)
        b.checkpoint()
        assert seen == [2]
        assert [ctx.exec_js("log.length") for ctx in contexts] == [0, 0, 1]
        a.checkpoint()
        assert seen == [2, 0, 1]
        assert [ctx.exec_js("log[0]") for ctx in contexts] == [0, 1, 2]
    finally:
        for ctx in contexts:
            ctx.close()
        a.close()
        b.close()
        isolate.close()


def test_queue_close_rejects_live_context_and_owner_thread_mismatch():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    ctx = isolate.context(object(), microtask_queue=queue)
    try:
        with pytest.raises(RuntimeError, match="associated contexts"):
            queue.close()
        with ThreadPoolExecutor(1) as pool:
            for operation in (queue.checkpoint, lambda: ctx.exec_js("1"), ctx.close):
                with pytest.raises(RuntimeError, match="owner thread"):
                    pool.submit(operation).result()
    finally:
        ctx.close()
        queue.close()
        queue.close()
        isolate.close()


def test_queue_rejects_other_isolate_and_closed_entry():
    left, right = pyv8.Isolate(), pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(left)
    try:
        with pytest.raises(ValueError, match="another isolate"):
            right.context(object(), microtask_queue=queue)
        queue.close()
        with pytest.raises(RuntimeError, match="closed"):
            queue.checkpoint()
    finally:
        queue.close()
        left.close()
        right.close()


def test_closed_context_jobs_do_not_execute_and_sibling_still_runs():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    left, right = [isolate.context(object(), microtask_queue=queue) for _ in range(2)]
    seen = []
    try:
        left.exec_js("Promise.resolve().then(()=>{throw Error('closed realm')})")
        left.enqueue_microtask(lambda: seen.append('closed'))
        right.exec_js("globalThis.ran=false; Promise.resolve().then(()=>ran=true)")
        left.close()
        queue.checkpoint()
        assert right.exec_js("ran") is True
        assert seen == []
    finally:
        left.close()
        right.close()
        queue.close()
        isolate.close()


def test_checkpoint_drains_followup_jobs_until_stable():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    ctx = isolate.context(object(), microtask_queue=queue)
    try:
        ctx.exec_js("globalThis.n=0; Promise.resolve().then(()=>n++).then(()=>n++)")
        queue.checkpoint()
        assert ctx.exec_js("n") == 2
    finally:
        ctx.close()
        queue.close()
        isolate.close()


def test_checkpoint_python_callback_and_reentrant_checkpoint():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    ctx = isolate.context(object(), microtask_queue=queue)
    seen = []
    try:
        def callback():
            seen.append('start')
            queue.checkpoint()
            seen.append('end')
        ctx.enqueue_microtask(callback)
        ctx.enqueue_microtask(lambda: seen.append('next'))
        queue.checkpoint()
        assert seen == ['start', 'end', 'next']
    finally:
        ctx.close()
        queue.close()
        isolate.close()


def test_infinite_microtasks_use_existing_watchdog_and_allow_next_operation():
    isolate = pyv8.Isolate()
    queue = pyv8.MicrotaskQueue(isolate)
    ctx = isolate.context(object(), microtask_queue=queue)
    try:
        ctx.exec_js("function again(){Promise.resolve().then(again)}; again()")
        with pytest.raises(pyv8.JavaScriptTerminated):
            queue.checkpoint(timeout=0.02)
        assert ctx.exec_js('1+1') == 2
    finally:
        ctx.close()
        queue.close()
        isolate.close()


def test_context_queue_change_requires_an_empty_entered_context_stack():
    isolate = pyv8.Isolate()
    left, right = pyv8.MicrotaskQueue(isolate), pyv8.MicrotaskQueue(isolate)
    ctx = isolate.context(object(), microtask_queue=left)
    try:
        ctx.exec_js('globalThis.value=1')
        ctx.set_microtask_queue(right)
        left.close()
        ctx.exec_js('Promise.resolve().then(()=>value=2)')
        assert ctx.exec_js('value') == 1
        right.checkpoint()
        assert ctx.exec_js('value') == 2
        errors = []
        def nested():
            with pytest.raises(RuntimeError, match='entered'):
                ctx.set_microtask_queue(pyv8.MicrotaskQueue(isolate))
            errors.append('rejected')
        ctx.enqueue_microtask(nested)
        right.checkpoint()
        assert errors == ['rejected']
    finally:
        ctx.close()
        left.close()
        right.close()
        isolate.close()
