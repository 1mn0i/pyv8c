import time
import pyv8
import pytest


def test_completed_ticket_cannot_terminate_next_operation():
    ctx = pyv8.Context(object())
    try:
        old = ctx.begin_operation(1)
        ctx.end_operation(old)
        current = ctx.begin_operation(1)
        assert not ctx.terminate_operation(old)
        assert ctx.exec_js('1+1') == 2
        ctx.end_operation(current)
    finally:
        ctx.close()


def test_pending_cancel_is_removed_with_its_exact_ticket():
    ctx = pyv8.Context(object())
    try:
        old = ctx.begin_operation(1)
        assert ctx.terminate_operation(old)
        ctx.end_operation(old)
        assert ctx.exec_js('2+2') == 4
    finally:
        ctx.close()


def test_deadlines_paused_before_inspector_start_do_not_expire():
    ctx = pyv8.Context(object())
    try:
        operation = ctx.begin_operation(0.03)
        paused = ctx._suspend_deadlines()
        time.sleep(0.06)
        ctx._resume_deadlines(paused)
        assert ctx.exec_js('3+3') == 6
        ctx.end_operation(operation)
    finally:
        ctx.close()


def test_deferred_context_close_keeps_the_running_operation_ticket():
    ctx = pyv8.Context(object())
    ticket = ctx.begin_operation(1)
    seen = []
    def close_inside_script():
        ctx.close()
        seen.append(ctx.terminate_operation(ticket))
    try:
        ctx.expose(closeInside=close_inside_script)
        with pytest.raises(pyv8.JavaScriptTerminated):
            ctx.exec_js('closeInside();while(true){}')
        assert seen == [True]
    finally:
        ctx.end_operation(ticket)
        ctx.close()
