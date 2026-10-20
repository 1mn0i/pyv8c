from pyv8c.foundation.tasks import TaskQueue, TaskSource
from pyv8c.foundation.executor import RealmExecutor
import threading


def test_cancelled_timer_head_does_not_end_ready_drain():
    queue = TaskQueue()
    seen = []
    for _ in range(5):
        queue.cancel(queue.schedule(lambda: seen.append('cancelled'), 0))
    queue.schedule(lambda: seen.append('ready'), 0)
    while queue.run_turn():
        pass
    assert seen == ['ready']
    assert queue.next_deadline() is None
    assert not queue.cancelled


def test_cancelled_future_timer_does_not_wake_idle_executor():
    queue = TaskQueue()
    queue.cancel(queue.schedule(lambda: None, 60))
    assert queue.next_deadline() is None
    assert not queue.timers


def test_self_posting_commands_cannot_starve_page_tasks():
    executor = RealmExecutor(name='command-fairness', auto_pump=True)
    queue = TaskQueue()
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    commands = []

    def command():
        commands.append(None)
        if not delivered.is_set() and len(commands) < 1000:
            executor.post(command)

    try:
        executor.attach_task_queue(queue)
        executor.post(lambda: (entered.set(), release.wait(2)))
        assert entered.wait(1)
        executor.post(command)
        queue.post(delivered.set)
        release.set()
        assert delivered.wait(2)
        executor.call(lambda: None)
        assert len(commands) < 1000
    finally:
        release.set()
        executor.close()


def test_one_task_per_selection_and_fifo_with_fair_sources():
    queue = TaskQueue()
    seen = []
    for source in TaskSource:
        for index in range(3):
            queue.post(lambda source=source, index=index: seen.append((source, index)), source)
    for count in range(1, 3 * len(TaskSource) + 1):
        assert queue.run_turn()
        assert len(seen) == count
    assert not queue.run_turn()
    assert {source for source, _ in seen[:len(TaskSource)]} == set(TaskSource)
    for source in TaskSource:
        assert [index for candidate, index in seen if candidate is source] == [0, 1, 2]


def test_parser_blocking_does_not_block_other_sources():
    class Host:
        def is_closed(self):
            return False
        def parser_blocked(self):
            return True
    queue = TaskQueue(Host())
    seen = []
    queue.post(lambda: seen.append('parser'), TaskSource.PARSER)
    queue.post(lambda: seen.append('network'), TaskSource.NETWORKING)
    queue.schedule(lambda: seen.append('timer'), 0)
    assert queue.run_turn()
    assert queue.run_turn()
    assert not queue.run_turn()
    assert set(seen) == {'network', 'timer'}


def test_post_and_close_cannot_leave_tasks_in_closed_queue():
    for _ in range(100):
        queue = TaskQueue()
        barrier = threading.Barrier(2)
        producer = threading.Thread(target=lambda: (barrier.wait(), queue.post(lambda: None)))
        producer.start()
        barrier.wait()
        queue.close()
        producer.join()
        assert not queue.pending()
        assert not queue.post(lambda: None)


def test_timer_cancelled_after_becoming_ready_does_not_hide_other_sources():
    queue = TaskQueue()
    seen = []
    queue.post(queue.cancel_timers)
    queue.post(lambda: seen.append('still-ready'))
    queue.schedule(lambda: seen.append('cancelled'), 0)
    while queue.run_turn():
        pass
    assert seen == ['still-ready']
    assert not queue.pending()


def test_timer_cancel_racing_selected_task_does_not_end_drain():
    queue = TaskQueue()
    seen = []
    original = queue._run_due_timers
    def cancel_before_execution():
        queue.cancel_timers()
        return original()
    queue._run_due_timers = cancel_before_execution
    queue.post(lambda: None)
    queue.post(lambda: seen.append('ready'))
    queue.schedule(lambda: seen.append('cancelled'), 0)
    while queue.run_turn():
        pass
    assert seen == ['ready']
    assert not queue.pending()


def test_checkpoint_failure_does_not_stop_owner_or_next_task():
    executor = RealmExecutor(name="checkpoint-failure", auto_pump=True)
    reported, delivered = threading.Event(), threading.Event()
    failure = RuntimeError("checkpoint failed")
    errors = []
    queue = TaskQueue()
    queue.report_error = lambda exc: (errors.append(exc), reported.set())
    calls = []

    def boundary():
        calls.append(None)
        if len(calls) == 1:
            raise failure

    try:
        executor.attach_task_queue(queue)
        queue.set_boundary(boundary)
        queue.post(lambda: None)
        queue.post(delivered.set)
        assert reported.wait(1)
        assert delivered.wait(1)
        assert errors == [failure]
        assert executor.call(lambda: 42) == 42
    finally:
        executor.close()


def test_html_timeout_nesting_clamps_after_six_initial_callbacks(monkeypatch):
    from pyv8c.foundation import tasks
    clock = [0.0]
    monkeypatch.setattr(tasks, '_now', lambda: clock[0])
    queue = TaskQueue()
    seen = []
    def callback():
        seen.append(clock[0])
        if len(seen) < 9:
            queue.schedule_html_timer(callback, 0)
    queue.schedule_html_timer(callback, 0)
    while queue.next_deadline() is not None:
        clock[0] += queue.next_deadline()
        assert queue.run_turn()
    assert seen == [0.0] * 6 + [0.004, 0.008, 0.012]
    assert not queue._timer_ids


def test_html_interval_nests_and_cancel_during_checkpoint_stops_rearming(monkeypatch):
    from pyv8c.foundation import tasks
    clock = [0.0]
    monkeypatch.setattr(tasks, '_now', lambda: clock[0])
    queue = TaskQueue()
    seen = []
    ident = queue.schedule_html_timer(lambda: seen.append(clock[0]), 0, repeat=True)
    queue.set_boundary(lambda: queue.cancel(ident) if len(seen) == 8 else None)
    while queue.next_deadline() is not None:
        clock[0] += queue.next_deadline()
        assert queue.run_turn()
    assert seen == [0.0] * 6 + [0.004, 0.008]
    assert not queue._timer_ids and not queue.cancelled


def test_timer_nesting_is_local_to_chrome_execution_context(monkeypatch):
    from pyv8c.foundation import tasks
    clock = [0.0]
    monkeypatch.setattr(tasks, '_now', lambda: clock[0])
    left, right = TaskQueue(), TaskQueue()
    seen = []
    def boundary():
        if len(seen) < 8:
            other = right if len(seen) % 2 else left
            other.schedule_html_timer(lambda: seen.append(clock[0]), 0)
    left.set_boundary(boundary)
    right.set_boundary(boundary)
    left.schedule_html_timer(lambda: seen.append(clock[0]), 0)
    for i in range(8):
        current = left if i % 2 == 0 else right
        clock[0] += current.next_deadline()
        assert current.run_turn()
    assert seen == [0.0] * 8
    left.set_boundary(None)
    left.post(lambda: left.schedule_html_timer(lambda: seen.append(clock[0]), 0))
    assert left.run_turn()
    assert left.next_deadline() == 0


def test_interval_deadline_grid_skips_missed_slots_before_callback(monkeypatch):
    from pyv8c.foundation import tasks
    clock = [0.0]
    monkeypatch.setattr(tasks, '_now', lambda: clock[0])
    queue = TaskQueue()
    queue.schedule_html_timer(lambda: None, 0.004, repeat=True)
    clock[0] = 0.010
    assert queue.run_turn()
    assert abs(queue.next_deadline() - 0.002) < 1e-12
    queue.cancel_timers()


def test_timer_cleanup_boundary_inherits_current_context_nesting(monkeypatch):
    from pyv8c.foundation import tasks
    clock = [0.0]
    monkeypatch.setattr(tasks, '_now', lambda: clock[0])
    queue = TaskQueue()
    seen = []
    def boundary():
        if len(seen) < 8:
            queue.schedule_html_timer(lambda: seen.append(clock[0]), 0)
    queue.set_boundary(boundary)
    queue.schedule_html_timer(lambda: seen.append(clock[0]), 0)
    while queue.next_deadline() is not None:
        clock[0] += queue.next_deadline()
        assert queue.run_turn()
    assert seen == [0.0] * 6 + [0.004, 0.008]
