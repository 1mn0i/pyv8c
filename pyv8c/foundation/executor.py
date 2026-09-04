"""Single-owner execution loop for a V8 realm.

V8 contexts are not made thread-safe by a Python lock.  A realm therefore
owns one executor thread; callers submit work to it and may synchronously wait
for the result.  The executor also drives the realm task queue when idle.
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future


class RealmExecutor:
    """Run all V8 and realm tasks on one dedicated thread."""

    def __init__(self, name="pyv8c-realm"):
        self._commands = queue.Queue()
        self._closed = threading.Event()
        self._submit_lock = threading.Lock()
        self._ready = threading.Event()
        self._owner_ident = None
        self._task_queue = None
        self._last_command = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if not self._ready.is_set():
            raise RuntimeError("realm executor failed to start")

    @property
    def thread(self):
        return self._thread

    @property
    def closed(self):
        return self._closed.is_set()

    def is_owner_thread(self):
        return threading.get_ident() == self._owner_ident

    def attach_task_queue(self, task_queue):
        """Attach the queue after PageServices and the Realm exist."""
        self._task_queue = task_queue
        self.wakeup()

    def wakeup(self):
        # A no-op command wakes the command wait without executing user code.
        if not self.closed:
            self._commands.put(None)

    def call(self, callback, *args, timeout=None, **kwargs):
        """Run *callback* on the owner thread and return or raise its result."""
        if self.is_owner_thread():
            return callback(*args, **kwargs)
        future = Future()
        with self._submit_lock:
            if self.closed:
                raise RuntimeError("realm executor is closed")
            self._commands.put((future, callback, args, kwargs))
        return future.result(timeout=timeout)

    def post(self, callback, *args, **kwargs):
        """Asynchronously execute a callback on the owner thread."""
        with self._submit_lock:
            if self.closed:
                return False
            self._commands.put((None, callback, args, kwargs))
        return True

    def _run(self):
        self._owner_ident = threading.get_ident()
        self._ready.set()
        while not self._closed.is_set():
            command = None
            try:
                command = self._commands.get(timeout=0.005)
            except queue.Empty:
                pass
            if command is not None:
                if command is _STOP:
                    break
                future, callback, args, kwargs = command
                self._last_command = time.monotonic()
                try:
                    result = callback(*args, **kwargs)
                except BaseException as exc:
                    if future is not None:
                        future.set_exception(exc)
                else:
                    if future is not None:
                        future.set_result(result)
            task_queue = self._task_queue
            if (task_queue is not None and self._commands.empty()
                    and time.monotonic() - self._last_command >= 0.01):
                task_queue._run_background_once(include_posted=False)
        self._closed.set()

    def close(self, timeout=5):
        with self._submit_lock:
            if self.closed:
                return
            self._closed.set()
            self._commands.put(_STOP)
            # Complete callers waiting on commands that will no longer run.
            while True:
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    break
                if isinstance(command, tuple) and command and command[0] is not None:
                    command[0].set_exception(RuntimeError("realm executor is closed"))
        if not self.is_owner_thread():
            self._thread.join(timeout)


_STOP = object()


__all__ = ["RealmExecutor"]
