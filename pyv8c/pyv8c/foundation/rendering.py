"""Owner-driven rendering opportunities, independent of HTML timer tasks."""
from .error_policy import control_exceptions
import itertools
import math
import time


class RenderingScheduler:
    def __init__(self, *, frame_rate=60.0, now=time.perf_counter):
        rate = float(frame_rate)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('frame_rate must be finite and positive')
        self.interval = 1.0 / rate
        self._now = now
        self._epoch = now()
        self._due = None
        self._lanes = {}
        self._pending_lanes = {}
        self._wake = lambda: None
        self.closed = False
        self.running = False

    def set_wakeup(self, wake):
        self._wake = wake

    def add(self, *, invoke, active, timestamp, report_error, order=lambda: (), owner=None):
        if self.closed:
            raise RuntimeError('rendering scheduler is closed')
        lane = AnimationFrameLane(self, invoke, active, timestamp, report_error, order, owner)
        self._lanes[lane] = None
        return lane

    def _pending(self):
        if not self._pending_lanes:
            return False
        return any(lane.active() for lane in tuple(self._pending_lanes))

    def _sync_pending(self, lane):
        """Update the request index on the owner after callback-map changes."""
        if lane.callbacks and not lane.closed:
            self._pending_lanes[lane] = None
        else:
            self._pending_lanes.pop(lane, None)

    def changed(self):
        if not self.closed and self._due is None and self._pending():
            now = self._now()
            tick = math.floor((now - self._epoch) / self.interval) + 1
            self._due = self._epoch + tick * self.interval
        self._wake()

    def next_deadline(self):
        if self.closed or not self._pending():
            self._due = None
            return None
        if self._due is None:
            self.changed()
        return max(0.0, self._due - self._now())

    def has_runnable_tasks(self, owners=None):
        if owners is None:
            return self.next_deadline() == 0.0
        # Host idle queries may run off-owner: observe without changing deadlines
        # or scheduling a wakeup. All state transitions stay on the owner thread.
        due = self._due
        return (not self.closed and due is not None and due <= self._now()
                and any(lane.owner in owners and lane.callbacks and lane.active()
                        for lane in tuple(self._pending_lanes)))

    def run_frame(self):
        if self.running or not self.has_runnable_tasks():
            return False
        stamp = self._due
        now = self._now()
        stamp += math.floor((now - stamp) / self.interval) * self.interval
        self._due = stamp + self.interval
        self.running = True
        try:
            # Chrome excludes idle documents before running callbacks. Handles
            # are still snapshotted separately when each selected document runs.
            documents = [lane for lane in self._pending_lanes if lane.active()]
            for lane in sorted(documents, key=lambda lane: lane.order()):
                if lane in self._lanes and lane.callbacks and lane.active():
                    lane.invoke(lane.run, stamp)
        finally:
            self.running = False
            if not self._pending():
                self._due = None
        return True

    def close(self):
        self.closed = True
        for lane in tuple(self._lanes):
            lane.close()
        self._due = None

    def cancel_owner(self, owner):
        for lane in tuple(self._lanes):
            if lane.owner is owner:
                lane.close()


class AnimationFrameLane:
    def __init__(self, scheduler, invoke, active, timestamp, report_error, order, owner):
        self.scheduler = scheduler
        self.invoke = invoke
        self.active = active
        self.timestamp = timestamp
        self.report_error = report_error
        self.order = order
        self.owner = owner
        self.callbacks = {}
        self._ids = itertools.count(1)
        self.closed = False

    def request(self, callback):
        if self.closed:
            return 0
        ident = next(self._ids)
        self.callbacks[ident] = callback
        self.scheduler._sync_pending(self)
        self.scheduler.changed()
        return ident

    def cancel(self, ident):
        self.callbacks.pop(ident, None)
        self.scheduler._sync_pending(self)
        self.scheduler.changed()

    def run(self, stamp):
        timestamp = self.timestamp(stamp)
        for ident in tuple(self.callbacks):
            if self.closed or not self.active():
                break
            callback = self.callbacks.pop(ident, None)
            self.scheduler._sync_pending(self)
            if callback is not None:
                # invoke owns each callback's budget and cleanup checkpoint.
                try:
                    self.invoke(self._callback, callback, timestamp)
                except control_exceptions():
                    raise
                except BaseException as error:
                    self.report_error(error)

    def _callback(self, callback, timestamp):
        try:
            callback(timestamp)
        except control_exceptions():
            raise
        except BaseException as error:
            self.report_error(error)

    def close(self):
        self.closed = True
        self.callbacks.clear()
        self.scheduler._sync_pending(self)
        self.scheduler._lanes.pop(self, None)
        self.scheduler.changed()
