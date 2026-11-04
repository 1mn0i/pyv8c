"""Wake owner executors when V8 posts foreground work.

One daemon waits on the native process-wide condition; posting never calls
Python. Source methods other than wakeup run on the isolate owner thread.
"""
import threading
import time

from .logging import logger


class _Notifications:
    def __init__(self):
        self.lock = threading.Lock()
        self.sources = set()
        self.thread = None

    def add(self, source):
        # Read before publishing: a post during registration must be visible to
        # the first wait, even if the executor has already gone back to sleep.
        version = source.isolate._foreground_version()
        with self.lock:
            self.sources.add(source)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, args=(version,),
                                               name='pyv8c-v8-notifications', daemon=True)
                self.thread.start()

    def remove(self, source):
        with self.lock:
            self.sources.discard(source)

    def _run(self, version):
        while True:
            with self.lock:
                sources = tuple(self.sources)
                if not sources:
                    self.thread = None
                    return
            try:
                version = sources[0].isolate._foreground_wait(version)
            except RuntimeError:
                # The selected isolate can close while the wait is being
                # entered. Another live isolate may still share the hub.
                time.sleep(0.001)
                continue
            except Exception:
                # A broken notification must not take the whole hub down:
                # every executor registered here would stop waking.
                logger.exception('foreground notification wait failed')
                time.sleep(0.01)
                continue
            with self.lock:
                sources = tuple(self.sources)
            for source in sources:
                try:
                    source.notify()
                except Exception:
                    logger.exception('foreground notification delivery failed')


_notifications = _Notifications()


class ForegroundSource:
    """An owner-thread view of one Isolate's native foreground queue."""
    def __init__(self, isolate, run_one, enabled=lambda: True):
        self.isolate = isolate
        self.run_one = run_one
        self.enabled = enabled
        self._wakeup = None

    def set_wakeup(self, wakeup):
        if wakeup is None:
            _notifications.remove(self)
            self._wakeup = None
        else:
            self._wakeup = wakeup
            _notifications.add(self)

    def notify(self):
        wakeup = self._wakeup
        if wakeup is not None:
            wakeup()

    def next_deadline(self):
        if (not self.enabled() or getattr(self.isolate, 'closed', False)):
            return None
        try:
            deadline = self.isolate._foreground_deadline()
        except RuntimeError:
            # The isolate can close between the enabled check and the native
            # query; an idle source is the safe answer for that race.
            return None
        return None if deadline is None else max(0.0, deadline)

    def has_runnable_tasks(self):
        deadline = self.next_deadline()
        return deadline is not None and deadline <= 0

    def run_turn(self):
        return self.run_one()
