from collections import deque
import gc
import weakref
import pytest
from pyv8c.foundation.rejections import RejectionTracker


class Promise:
    pass


class State:
    def __init__(self):
        self.entries = weakref.WeakKeyDictionary()
        self.counter = 0
    def identity(self, promise):
        if promise not in self.entries:
            self.counter += 1
            self.entries[promise] = [self.counter, False, None]
        return self.entries[promise][0]
    def mark_notified(self, promise, reason):
        self.entries[promise][1:] = [True, reason]
    def take_notified(self, promise):
        entry = self.entries[promise]
        found, reason = entry[1:]
        entry[1:] = [False, None]
        return found, reason
    def close(self):
        self.entries.clear()


def setup_tracker():
    tasks, events = deque(), []
    state = State()
    tracker = RejectionTracker(lambda *event: events.append(event), lambda task: tasks.append(task), state)
    return tracker, tasks, events, state


def test_checkpoint_queues_notification_and_preserves_native_hash_collisions():
    tracker, tasks, events, _ = setup_tracker()
    left, right = Promise(), Promise()
    tracker.record('unhandledrejection', 7, left, 'left')
    tracker.record('unhandledrejection', 7, right, 'right')
    tracker.flush()
    assert events == []
    assert len(tasks) == 1
    tasks.popleft()()
    assert events == [('unhandledrejection', left, 'left'), ('unhandledrejection', right, 'right')]
    assert not tracker._pending and not tracker._queued


@pytest.mark.parametrize('after_checkpoint', [False, True])
def test_handler_before_notification_suppresses_both_events(after_checkpoint):
    tracker, tasks, events, _ = setup_tracker()
    promise = Promise()
    tracker.record('unhandledrejection', 1, promise, 'reason')
    if after_checkpoint:
        tracker.flush()
    tracker.record('rejectionhandled', 1, promise, None)
    tracker.flush()
    while tasks:
        tasks.popleft()()
    assert events == []


def test_late_handler_retains_original_reason_and_gets_a_separate_task():
    tracker, tasks, events, _ = setup_tracker()
    promise, reason = Promise(), object()
    tracker.record('unhandledrejection', 1, promise, reason)
    tracker.flush()
    tasks.popleft()()
    tracker.record('rejectionhandled', 1, promise, None)
    assert len(events) == 1
    tasks.popleft()()
    assert events[-1] == ('rejectionhandled', promise, reason)
    tracker.record('rejectionhandled', 1, promise, None)
    assert not tasks


def test_closed_tracker_drops_tasks_without_holding_promises_alive():
    tracker, tasks, _, _ = setup_tracker()
    promise = Promise()
    ref = weakref.ref(promise)
    tracker.record('unhandledrejection', 1, promise, 'reason')
    tracker.flush()
    tracker.close()
    del promise
    gc.collect()
    assert ref() is None
    tasks.popleft()()
    tracker.close()


@pytest.mark.parametrize('failure', [False, RuntimeError('submit failed')])
def test_submission_failure_releases_queued_payload(failure):
    def post(_task):
        if isinstance(failure, BaseException):
            raise failure
        return False
    tracker = RejectionTracker(lambda *_: None, post, State())
    promise = Promise()
    tracker.record('unhandledrejection', 1, promise, 'reason')
    if isinstance(failure, BaseException):
        with pytest.raises(RuntimeError, match='submit failed'):
            tracker.flush()
    else:
        tracker.flush()
    assert not tracker._pending and not tracker._queued and not tracker._handled


def test_catch_installed_inside_unhandled_event_does_not_send_handled():
    tracker, tasks, events, _ = setup_tracker()
    promise = Promise()
    def publish(*event):
        events.append(event)
        tracker.record('rejectionhandled', 1, promise, None)
    tracker._publish = publish
    tracker.record('unhandledrejection', 1, promise, 'reason')
    tracker.flush()
    tasks.popleft()()
    assert events == [('unhandledrejection', promise, 'reason')]
    assert not tasks and not tracker._handled


def test_pending_subscription_tracks_transitions_replacement_and_close():
    tracker, tasks, _, _ = setup_tracker()
    events = []
    first, second = Promise(), Promise()
    tracker.record('unhandledrejection', 1, first, 'one')
    tracker.set_pending_listener(lambda t, pending: events.append(('old', t, pending)))
    tracker.record('unhandledrejection', 2, second, 'two')
    tracker.record('rejectionhandled', 1, first, None)
    assert events == [('old', tracker, True)]
    tracker.set_pending_listener(lambda t, pending: events.append(('new', t, pending)))
    tracker.flush()
    assert events == [('old', tracker, True), ('old', tracker, False),
                      ('new', tracker, True), ('new', tracker, False)]
    assert len(tasks) == 1
    tracker.record('unhandledrejection', 3, Promise(), 'three')
    tracker.close()
    tracker.close()
    assert events[-2:] == [('new', tracker, True), ('new', tracker, False)]
    assert tracker._pending_listener is None


def test_last_handler_and_reentrant_submission_preserve_candidate_membership():
    tracker, _, _, _ = setup_tracker()
    candidates = {}
    def changed(t, pending):
        if pending:
            candidates[t] = None
        else:
            candidates.pop(t, None)
    tracker.set_pending_listener(changed)
    promise = Promise()
    tracker.record('unhandledrejection', 1, promise, 'one')
    tracker.record('rejectionhandled', 1, promise, None)
    assert not candidates
    def post(_callback):
        tracker.record('unhandledrejection', 2, Promise(), 'during post')
        return False
    tracker._post = post
    tracker.record('unhandledrejection', 3, Promise(), 'three')
    tracker.flush()
    assert tracker in candidates and tracker._pending and not tracker._queued
    tracker.close()
    assert not candidates
