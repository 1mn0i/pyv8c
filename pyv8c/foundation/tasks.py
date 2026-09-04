"""Per-page task, timer, microtask, and pending-work scheduler."""

import heapq
import itertools
import queue
import threading
import time
from collections import deque

from .errors import as_js_error


class TaskQueue:
    def __init__(self, owner):
        self.owner = owner
        self.timers = []
        self.counter = itertools.count(1)
        self.cancelled = set()
        self.microtasks = deque()
        self.posted = queue.Queue()
        self.errors = []
        self.condition = threading.Condition()
        self.network_pending = 0
        self.worker_pending = 0
        self._closed = False

    def run_callback(self, callback, *args):
        """Run one queued task, keeping a faulty task from aborting the rest."""
        try:
            return callback(*args)
        except BaseException as exc:
            self.errors.append(as_js_error(exc))
            return None

    def begin_network(self):
        with self.condition:
            self.network_pending += 1

    def complete_network(self):
        with self.condition:
            self.network_pending = max(0, self.network_pending - 1)
            self.condition.notify_all()

    def begin_worker(self):
        with self.condition:
            self.worker_pending += 1

    def complete_worker(self):
        with self.condition:
            self.worker_pending = max(0, self.worker_pending - 1)
            self.condition.notify_all()

    def post(self, callback):
        if not self.owner.closed and not self._closed:
            self.posted.put(callback)
            with self.condition:
                self.condition.notify_all()

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
        return ident

    def cancel(self, ident):
        self.cancelled.add(int(ident))

    def _run_background_once(self, *, include_posted=True):
        """Drain background-safe work without blocking the realm executor.

        The executor uses ``include_posted=False`` so network and worker
        deliveries retain their explicit ``run_until_idle`` boundary while
        ordinary timers continue to behave like browser task sources.
        """
        if self._closed or self.owner.closed:
            return
        # A parser-blocking external script deliberately keeps the document
        # observable in the loading state until the embedding API pumps the
        # queue (the same boundary used by the legacy synchronous API).
        navigation = getattr(self.owner, "navigation", None)
        if navigation is not None and navigation.parser_is_blocked():
            return
        if include_posted:
            while True:
                try:
                    callback = self.posted.get_nowait()
                except queue.Empty:
                    break
                if not self.owner.closed:
                    self.run_callback(callback)
            while self.microtasks:
                self.run_callback(self.microtasks.popleft())
        now = time.monotonic()
        while self.timers and self.timers[0][0] <= now:
            due, ident, callback, interval = heapq.heappop(self.timers)
            if ident in self.cancelled:
                continue
            self.run_callback(callback)
            if interval is not None and ident not in self.cancelled and not self._closed:
                heapq.heappush(
                    self.timers,
                    (max(due + interval, time.monotonic()), ident, callback, interval),
                )
            now = time.monotonic()
        if include_posted:
            while self.microtasks:
                self.run_callback(self.microtasks.popleft())

    def run_until_idle(self, timeout=0):
        deadline = time.monotonic() + float(timeout or 0)
        while self.microtasks or self.timers or not self.posted.empty() or (
            timeout and (self.network_pending or self.worker_pending)
        ):
            while True:
                try:
                    callback = self.posted.get_nowait()
                except queue.Empty:
                    break
                if not self.owner.closed:
                    self.run_callback(callback)
            while self.microtasks:
                self.run_callback(self.microtasks.popleft())
            if not self.timers:
                if self.posted.empty() and not self.microtasks:
                    if timeout and time.monotonic() < deadline:
                        with self.condition:
                            self.condition.wait(min(0.01, max(0, deadline - time.monotonic())))
                        if not self.posted.empty():
                            continue
                    break
                continue
            due, ident, callback, interval = self.timers[0]
            now = time.monotonic()
            if due > now:
                if not timeout or now >= deadline:
                    break
                with self.condition:
                    self.condition.wait(min(due - now, max(0, deadline - now)))
                continue
            heapq.heappop(self.timers)
            if ident in self.cancelled:
                continue
            self.run_callback(callback)
            if interval is not None and ident not in self.cancelled:
                heapq.heappush(
                    self.timers,
                    (max(due + interval, time.monotonic()), ident, callback, interval),
                )
            if timeout and time.monotonic() >= deadline:
                break

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
        while True:
            try:
                self.posted.get_nowait()
            except queue.Empty:
                break
        with self.condition:
            self.condition.notify_all()
