"""Per-page task, timer, microtask, and pending-work scheduler.

A realm owns one :class:`TaskQueue`. The realm executor drives it through
:meth:`TaskQueue.run_turn`, which performs exactly one event-loop turn:

1. one parser task (only while the parser is not blocked),
2. one posted task (network / worker / iframe delivery),
3. a microtask checkpoint,
4. all due timers, each followed by a microtask checkpoint.

Returning after a single turn lets the executor interleave its own commands
with page work, and the two checkpoints mirror the WHATWG ordering of
"one macrotask, then drain the microtask queue".
"""

from __future__ import annotations

import enum
import heapq
import itertools
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Protocol, runtime_checkable

from .errors import as_js_error


class TaskSource(enum.Enum):
    """Task queues drained by one realm event-loop turn, in priority order."""

    COMMAND = "command"       # executor-delivered evaluate/goto/close/DevTools commands
    PARSER = "parser"         # parser resumption, gated by the parser-blocking state
    POSTED = "posted"         # network / worker / iframe completion callbacks
    TIMER = "timer"           # setTimeout / setInterval
    MICROTASK = "microtask"   # promise reactions and queueMicrotask


@runtime_checkable
class TaskHost(Protocol):
    """The only thing :class:`TaskQueue` may ask of its owning realm.

    ``TaskQueue`` lives in ``foundation`` and must never reach back into
    ``platform``.  The page supplies this protocol instead of itself, which
    keeps the dependency direction one-way and allows the queue to be
    constructed and tested in isolation.
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
    """One realm's task sources, timers, microtasks and pending-work counters."""

    def __init__(self, host=None, *, error_sink: Callable[[BaseException], None] | None = None):
        self._host = host if host is not None else _NullTaskHost()
        self._error_sink = error_sink
        self.timers = []
        self.counter = itertools.count(1)
        self.cancelled = set()
        self.microtasks = deque()
        self.posted = queue.Queue()
        self.errors = []
        self.condition = threading.Condition()
        self.network_pending = 0
        self.worker_pending = 0
        self._parser = deque()
        self._closed = False
        self._wakeup = None

    # -- host ------------------------------------------------------------

    @property
    def closed(self):
        return self._closed or self._host.is_closed()

    def set_error_sink(self, sink):
        """Install the reporter notified for every task exception."""
        self._error_sink = sink

    def set_wakeup(self, wakeup):
        """Install the callback that rouses the realm executor.

        The executor sleeps until the next timer; without this hook a task
        posted from a worker or network thread would only be noticed on the
        executor's safety timeout.
        """
        self._wakeup = wakeup

    def _signal(self):
        """Wake waiters on this queue and, through it, the realm executor."""
        with self.condition:
            self.condition.notify_all()
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup()

    # -- task execution ---------------------------------------------------

    def run_callback(self, callback, *args):
        """Run one queued task, keeping a faulty task from aborting the rest."""
        try:
            return callback(*args)
        except BaseException as exc:
            self._report_error(exc)
            return None

    def _report_error(self, exc):
        """Record a task exception and hand it to the realm's reporter."""
        error = as_js_error(exc)
        with self.condition:
            self.errors.append(error)
        sink = self._error_sink
        if sink is not None:
            try:
                sink(error)
            except BaseException:
                # Reporting must never break the turn that produced the error.
                pass

    def drain_task_errors(self):
        """Take and clear the task exceptions collected so far."""
        with self.condition:
            errors = tuple(self.errors)
            self.errors.clear()
        return errors

    # -- pending work -----------------------------------------------------

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

    # -- posting -----------------------------------------------------------

    def post(self, callback, source: TaskSource = TaskSource.POSTED):
        """Queue *callback* on the given task source.

        Parser work is held separately so a parser-blocking script can gate
        resumption without freezing network delivery or timers.
        """
        if self.closed:
            return False
        if source is TaskSource.PARSER:
            self._parser.append(callback)
        else:
            self.posted.put(callback)
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
            (time.monotonic() + delay, ident, callback, delay if repeat else None),
        )
        self._signal()
        return ident

    def cancel(self, ident):
        self.cancelled.add(int(ident))

    # -- event loop turns ---------------------------------------------------

    def next_deadline(self):
        """Seconds until the next timer is due, or ``None`` when none exists.

        The realm executor uses this as its idle wait timeout so timers fire
        on time without polling.
        """
        if self.timers:
            return max(0.0, self.timers[0][0] - time.monotonic())
        return None

    @staticmethod
    def _take(buffer):
        try:
            if isinstance(buffer, queue.Queue):
                return buffer.get_nowait()
            return buffer.popleft()
        except (queue.Empty, IndexError):
            return None

    def _drain_microtasks(self):
        ran = False
        while self.microtasks:
            self.run_callback(self.microtasks.popleft())
            ran = True
        return ran

    def _run_due_timers(self):
        ran = False
        # A repeating zero-delay timer must not monopolise the realm.  One
        # timer callback per turn gives posted work, inspector commands and
        # microtasks a chance to run between callbacks.
        if self.timers and self.timers[0][0] <= time.monotonic():
            due, ident, callback, interval = heapq.heappop(self.timers)
            if ident in self.cancelled:
                self.cancelled.discard(ident)
            else:
                self.run_callback(callback)
                ran = True
                if interval is not None and ident not in self.cancelled and not self._closed:
                    # The HTML timer algorithm clamps a repeating timer to a
                    # positive delay.  Avoid re-queuing an interval at the
                    # same monotonic timestamp for delay=0.
                    interval = max(interval, 0.001)
                    heapq.heappush(
                        self.timers,
                        (max(due + interval, time.monotonic() + interval), ident, callback, interval),
                    )
                self._drain_microtasks()
        return ran

    def run_turn(self, *, allow_parser: bool = True) -> bool:
        """Perform one event-loop turn; return ``True`` when work happened.

        Must be called from the realm owner thread.  The executor interleaves
        these turns with its own commands, so a long-running page never
        starves pending ``evaluate``/``close`` requests.
        """
        if self.closed:
            return False
        worked = False
        if allow_parser and not self._host.parser_blocked():
            callback = self._take(self._parser)
            if callback is not None:
                self.run_callback(callback)
                worked = True
        callback = self._take(self.posted)
        if callback is not None:
            self.run_callback(callback)
            worked = True
        worked = self._drain_microtasks() or worked
        worked = self._run_due_timers() or worked
        worked = self._drain_microtasks() or worked
        return worked

    def has_ready_work(self):
        """Anything a turn would execute right now, excluding future timers."""
        if self.closed:
            return False
        if self.microtasks:
            return True
        if self.posted.qsize():
            return True
        if self._parser and not self._host.parser_blocked():
            return True
        return bool(self.timers) and self.timers[0][0] <= time.monotonic()

    def pump_until(self, predicate, timeout=0):
        """Turn the loop until ``predicate()`` holds or *timeout* expires.

        Runs on the realm owner thread.  Waiting is bounded by the next timer
        deadline and interrupted by any arriving task, so a caller waiting for
        a document lifecycle state wakes on the event that produces it rather
        than re-checking on a fixed interval.
        """
        deadline = time.monotonic() + float(timeout or 0)
        while True:
            if predicate():
                return True
            if self.closed:
                return predicate()
            self.run_turn()
            if predicate():
                return True
            if not timeout:
                return predicate()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return predicate()
            hint = self.next_deadline()
            wait_for = min(hint, remaining) if hint is not None else min(0.05, remaining)
            with self.condition:
                if not self.closed:
                    self.condition.wait(max(0.0, wait_for))

    def run_until_idle(self, timeout=0):
        """Block until the realm has no ready work left.

        This is a test driver only: it never decides *what* runs, it just
        keeps turning the loop (already driven by the realm executor) until
        the queues drain or *timeout* expires.
        """
        deadline = time.monotonic() + float(timeout or 0)
        while True:
            while self.run_turn():
                if timeout and time.monotonic() >= deadline:
                    return
            if self.closed:
                return
            if not timeout:
                return
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return
            deadline_hint = self.next_deadline()
            if deadline_hint is None and not (self.network_pending or self.worker_pending):
                return
            wait_for = min(0.01, remaining) if deadline_hint is None else min(deadline_hint, remaining)
            with self.condition:
                if not self.closed:
                    self.condition.wait(max(0.0, wait_for))

    def close(self):
        self._closed = True
        self.timers.clear()
        self.microtasks.clear()
        self.cancelled.clear()
        # Async network/worker completions may arrive after the Page closes.
        # Drop their wait state as part of teardown so a closed page cannot
        # retain stale pending work or keep a waiter blocked indefinitely.
        self.network_pending = 0
        self.worker_pending = 0
        self._parser.clear()
        while True:
            try:
                self.posted.get_nowait()
            except queue.Empty:
                break
        self._signal()


__all__ = ["TaskHost", "TaskQueue", "TaskSource"]
