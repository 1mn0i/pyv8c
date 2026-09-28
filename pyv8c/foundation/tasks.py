"""Per-page task, timer, and pending-work scheduler.

:meth:`TaskQueue.run_turn` performs exactly one event-loop turn -- one parser task
(while unblocked), one posted task, at most one due timer -- so the executor can
interleave its own commands with page work.  Microtasks are not a source here: the
realm's V8 context owns that queue.
"""

from __future__ import annotations

import enum
import heapq
import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

from . import timer_resolution
from .errors import as_js_error

#: Timed on the finest monotonic clock Python exposes: Windows' ``time.monotonic()``
#: moves once per 64 Hz tick, so a 5 ms timer could only be seen 15.6 ms late; on
#: platforms where ``monotonic`` is already fine grained ``perf_counter`` is the same clock.
_now = time.perf_counter


class TaskSource(enum.Enum):
    """Task queues drained by one realm event-loop turn, in priority order.

    Only sources callers post into are listed: timers live in this queue's own
    deadline-ordered heap and microtasks belong to V8.  Embedder commands travel on
    the executor's own queue, drained before any turn, so page work cannot starve them.
    """

    PARSER = "parser"         # parser resumption, gated by the parser-blocking state
    POSTED = "posted"         # network / worker / iframe completion callbacks


class TaskHost(Protocol):
    """The only thing :class:`TaskQueue` may ask of its owning realm.

    ``TaskQueue`` lives in ``foundation`` and must never reach back into ``platform``,
    so the page supplies this protocol and the queue can be built and tested alone.
    """

    def is_closed(self) -> bool: ...
    def parser_blocked(self) -> bool: ...


class _NullTaskHost:
    """Stand-in host used when a queue is built without an owning realm."""

    def is_closed(self) -> bool:
        return False

    def parser_blocked(self) -> bool:
        return False


class TaskQueue:
    """One realm's task sources, timers and pending-work counters."""

    def __init__(self, host=None, *, error_sink: Callable[[BaseException], None] | None = None):
        self._host = host if host is not None else _NullTaskHost()
        self._error_sink = error_sink
        self.timers: list[tuple[float, int, Callable[..., object], float | None]] = []
        self.counter = itertools.count(1)
        self.cancelled: set[int] = set()
        # Single-threaded pop with append-only producers; ``_signal`` fences a sleep.
        self.posted: deque[Callable[..., object]] = deque()
        self.errors: list[BaseException] = []
        self.condition = threading.Condition()
        self.network_pending = 0
        self.worker_pending = 0
        self._parser: deque[Callable[..., object]] = deque()
        self._closed = False
        self._wakeup = None
        # Bumped on delivery so a waiter can spot work that arrived before it slept.
        self._signal_seq = 0

    @property
    def closed(self):
        return self._closed or self._host.is_closed()

    def set_error_sink(self, sink):
        """Install the reporter notified for every task exception."""
        self._error_sink = sink

    def set_wakeup(self, wakeup):
        """Install the callback that rouses the realm executor.

        Without it a task posted from a worker or network thread would only be noticed
        on the executor's safety timeout.
        """
        self._wakeup = wakeup

    def _signal(self):
        """Wake waiters on this queue and, through it, the realm executor.

        The sequence is bumped *inside* the lock so a waiter that samples it before
        sleeping can tell whether anything arrived in between.
        """
        with self.condition:
            self._signal_seq += 1
            self.condition.notify_all()
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup()

    def run_callback(self, callback, *args):
        """Run one queued task, keeping a faulty task from aborting the rest."""
        try:
            return callback(*args)
        except BaseException as exc:  # reported below
            self._report_error(exc)
            return None

    def report_error(self, exc):
        """Record an exception against this realm and notify the sink.

        Public counterpart of :meth:`_report_error`, for collaborators such as the
        executor that need the same bookkeeping without a private method.
        """
        self._report_error(exc)

    def _report_error(self, exc):
        """Record a task exception and hand it to the realm's reporter."""
        error = as_js_error(exc)
        with self.condition:
            self.errors.append(error)
        sink = self._error_sink
        if sink is not None:
            try:
                sink(error)
            except BaseException:  # reporting must never break the turn
                pass

    def drain_task_errors(self):
        """Take and clear the task exceptions collected so far."""
        with self.condition:
            errors = tuple(self.errors)
            self.errors.clear()
        return errors

    def begin_network(self):
        with self.condition:
            self.network_pending += 1

    def complete_network(self):
        with self.condition:
            self.network_pending = max(0, self.network_pending - 1)
        self._signal()

    def begin_worker(self):
        with self.condition:
            self.worker_pending += 1

    def complete_worker(self):
        with self.condition:
            self.worker_pending = max(0, self.worker_pending - 1)
        self._signal()

    def post(self, callback, source: TaskSource = TaskSource.POSTED):
        """Queue *callback* on the given task source.

        Parser work is held separately so a parser-blocking script can gate resumption
        without freezing network delivery or timers.
        """
        if self.closed:
            return False
        if source is TaskSource.PARSER:
            self._parser.append(callback)
        else:
            self.posted.append(callback)
        self._signal()
        return True

    def schedule(self, callback, delay, repeat=False):
        if self._closed:
            return 0
        ident = next(self.counter)
        try:
            delay = max(0.0, float(delay))
        except (TypeError, ValueError):
            delay = 0.0
        heapq.heappush(
            self.timers,
            (_now() + delay, ident, callback, delay if repeat else None),
        )
        self._signal()
        return ident

    def cancel(self, ident):
        self.cancelled.add(int(ident))

    def next_deadline(self):
        """Seconds until the next timer is due, or ``None`` when none exists.

        The executor uses this as its idle wait timeout so timers fire without polling.
        """
        if self.timers:
            return max(0.0, self.timers[0][0] - _now())
        return None

    def _run_due_timers(self):
        ran = False
        # One timer per turn: a repeating zero-delay timer must not monopolise the realm.
        if self.timers and self.timers[0][0] <= _now():
            due, ident, callback, interval = heapq.heappop(self.timers)
            if ident in self.cancelled:
                self.cancelled.discard(ident)
            else:
                self.run_callback(callback)
                ran = True
                if interval is not None and ident not in self.cancelled and not self._closed:
                    # The HTML timer algorithm clamps a repeating timer to a positive delay.
                    interval = max(interval, 0.001)
                    heapq.heappush(
                        self.timers,
                        (max(due + interval, _now() + interval), ident, callback, interval),
                    )
        return ran

    def run_turn(self, *, allow_parser: bool = True) -> bool:
        """Perform one event-loop turn; return ``True`` when work happened.

        Must be called from the realm owner thread; the executor interleaves these
        turns with its own commands so a long page never starves ``evaluate``/``close``.
        """
        if self.closed:
            return False
        worked = False
        # Only ask about parser-blocking when there is parser work to gate: it is the
        # most expensive check in a turn.
        if allow_parser and self._parser and not self._host.parser_blocked():
            self.run_callback(self._parser.popleft())
            worked = True
        if self.posted:
            self.run_callback(self.posted.popleft())
            worked = True
        return self._run_due_timers() or worked

    def pump_until(self, predicate, timeout=0):
        """Turn the loop until ``predicate()`` holds or *timeout* expires.

        Runs on the realm owner thread; waits are bounded by the next timer deadline and
        interrupted by any arriving task, so a caller waiting for a document lifecycle
        state wakes on the event that produces it.

        The sleep gate samples the delivery sequence *before* the turn, so a delivery
        racing between the turn and the sample is not slept out.
        """
        deadline = _now() + float(timeout or 0)
        while True:
            seq = self._signal_seq
            if predicate():
                return True
            if self.closed:
                return predicate()
            self.run_turn()
            if predicate():
                return True
            if not timeout:
                return predicate()
            remaining = deadline - _now()
            if remaining <= 0:
                return predicate()
            hint = self.next_deadline()
            wait_for = min(hint, remaining) if hint is not None else min(0.05, remaining)
            # Bounded waits, so ask for the finer platform timer while they last.
            with timer_resolution.hold(), self.condition:
                if not self.closed and self._signal_seq == seq:
                    self.condition.wait(max(0.0, wait_for))

    def run_until_idle(self, timeout=0):
        """Block until the realm has no ready work left.

        A test driver only: it never decides *what* runs, it just keeps turning the loop
        until the queues drain or *timeout* expires.
        """
        deadline = _now() + float(timeout or 0)
        while True:
            # Sample before the drain: a delivery during this iteration bumps the
            # sequence and the gate below skips the sleep.
            seq = self._signal_seq
            while self.run_turn():
                if timeout and _now() >= deadline:
                    return
            if self.closed:
                return
            if not timeout:
                return
            remaining = max(0.0, deadline - _now())
            if remaining == 0.0:
                return
            deadline_hint = self.next_deadline()
            if deadline_hint is None and not (self.network_pending or self.worker_pending):
                return
            wait_for = min(0.01, remaining) if deadline_hint is None else min(deadline_hint, remaining)
            with timer_resolution.hold(), self.condition:
                if not self.closed and self._signal_seq == seq:
                    self.condition.wait(max(0.0, wait_for))

    def close(self):
        self._closed = True
        self.timers.clear()
        self.cancelled.clear()
        # Completions may arrive after close: drop wait state so a waiter cannot stay blocked.
        self.network_pending = 0
        self.worker_pending = 0
        self._parser.clear()
        self.posted.clear()
        self._signal()


__all__ = ["TaskHost", "TaskQueue", "TaskSource"]
