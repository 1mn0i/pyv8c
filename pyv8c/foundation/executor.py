"""Single-owner execution loop for a V8 realm.

V8 contexts are not made thread-safe by a Python lock.  A realm therefore
owns one executor thread; callers submit work to it and may synchronously wait
for the result.  When no command is pending the executor turns the realm's
event loop, so network, worker and iframe callbacks progress on their own
instead of waiting for the embedding API to pump the queue.

Waiting is event driven: the thread sleeps on a :class:`threading.Event`
until either a command arrives or the realm's next timer is due.  A realm
with nothing to do performs no wake-ups at all.
"""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError

from .errors import RealmBusyError


def _env_flag(name, default=True):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _default_call_timeout():
    """Seconds a synchronous ``call()`` may wait before raising.

    ``PYV8C_CALL_TIMEOUT`` overrides it; ``0`` or a negative value disables the
    bound.  The default is deliberately far above every documented operation
    (``goto`` caps at 30s, DevTools commands at 10s) so it only trips on a
    genuinely wedged realm.
    """
    raw = os.environ.get("PYV8C_CALL_TIMEOUT")
    if raw is None:
        return 60.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 60.0
    return None if value <= 0 else value


#: Safety bound for the idle wait.  Wake-ups are event driven, so this only
#: covers a missed notification; sleeping this long costs nothing.
IDLE_WAIT_MAX = 0.05

_UNSET = object()


def _swallow(future):
    """Retrieve a late result so CPython does not warn about it."""
    if not future.cancelled():
        future.exception()


class RealmExecutor:
    """Run all V8 and realm tasks on one dedicated thread."""

    def __init__(self, name="pyv8c-realm", *, auto_pump=None, default_timeout=_UNSET):
        self._commands = queue.Queue()
        self._closed = threading.Event()
        self._submit_lock = threading.Lock()
        self._ready = threading.Event()
        self._wake = threading.Event()
        self._owner_ident = None
        self._task_queue = None
        self._paused = False
        self._pause_lock = threading.Lock()
        self.auto_pump = _env_flag("PYV8C_AUTO_PUMP") if auto_pump is None else bool(auto_pump)
        self.default_timeout = _default_call_timeout() if default_timeout is _UNSET else default_timeout
        self._close_diagnostics = []
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        # Start without waiting for the thread to be scheduled.  The command
        # queue is unbounded and thread safe, so post/call/close behave
        # identically whether the thread has run yet or not; call() carries its
        # own timeout for the pathological case.  Blocking here would let a
        # realm thread inside a V8 callback (e.g. constructing a Worker) stall
        # on the GIL while another thread holding it waits for this realm's
        # isolate lock -- a deadlock once more than one page is live.
        self._thread.start()

    @property
    def thread(self):
        return self._thread

    @property
    def closed(self):
        return self._closed.is_set()

    @property
    def paused(self):
        with self._pause_lock:
            return self._paused

    def is_owner_thread(self):
        return threading.get_ident() == self._owner_ident

    def attach_task_queue(self, task_queue):
        """Attach the queue after services and the Realm exist."""
        self._task_queue = task_queue
        # The queue wakes this thread directly; without it a timer or a
        # network delivery could only be noticed on the safety timeout.
        setter = getattr(task_queue, "set_wakeup", None)
        if callable(setter):
            setter(self.wakeup)
        self.wakeup()

    def set_paused(self, paused):
        """Suspend event-loop turns while the inspector holds the realm.

        Commands keep running: an attached debugger evaluates expressions
        through them.  Only page work is held so it cannot race the pause
        loop for the owner thread.
        """
        with self._pause_lock:
            self._paused = bool(paused)
        self.wakeup()

    def wakeup(self):
        # A no-op command wakes the command wait without executing user code.
        self._wake.set()

    def call(self, callback, *args, timeout=_UNSET, **kwargs):
        """Run *callback* on the owner thread and return or raise its result.

        Raises :class:`RealmBusyError` when the owner thread does not pick the
        command up within *timeout* (default: see :func:`_default_call_timeout`).
        A wedged realm must surface as an error rather than hang the caller.
        """
        if self.is_owner_thread():
            return callback(*args, **kwargs)
        if timeout is _UNSET:
            timeout = self.default_timeout
        future = Future()
        with self._submit_lock:
            if self.closed:
                raise RuntimeError("realm executor is closed")
            self._commands.put((future, callback, args, kwargs))
        self.wakeup()
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            # A command which is still queued can be cancelled safely.  This
            # prevents a timed-out navigation/evaluate from running later and
            # mutating a page after its caller has already handled the error.
            # Once execution has started Python/V8 has no safe pre-emption
            # point; in that case retain the late-result drain and report the
            # timeout without pretending the callback was interrupted.
            cancelled = future.cancel()
            if not cancelled:
                future.add_done_callback(_swallow)
            raise RealmBusyError(
                f"realm executor did not complete {getattr(callback, '__name__', callback)!r} "
                f"within {timeout}s"
                + (" (command cancelled)" if cancelled else " (already running)")
            ) from None

    def post(self, callback, *args, **kwargs):
        """Asynchronously execute a callback on the owner thread."""
        with self._submit_lock:
            if self.closed:
                return False
            self._commands.put((None, callback, args, kwargs))
        self.wakeup()
        return True

    # -- loop ------------------------------------------------------------

    def _next_command(self):
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def _execute(self, command):
        future, callback, args, kwargs = command
        if future is not None and not future.set_running_or_notify_cancel():
            # The caller timed out while this command was waiting in the
            # queue.  Do not execute it as a ghost side effect.
            return
        try:
            result = callback(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - mirrored onto the future
            if future is not None:
                future.set_exception(exc)
        else:
            if future is not None:
                future.set_result(result)

    def _idle_timeout(self):
        """How long the loop may sleep before it must look again."""
        if self._paused:
            return IDLE_WAIT_MAX
        task_queue = self._task_queue
        if task_queue is None or not self.auto_pump:
            return IDLE_WAIT_MAX
        deadline = task_queue.next_deadline()
        if deadline is None:
            return IDLE_WAIT_MAX
        return min(deadline, IDLE_WAIT_MAX)

    def _run_turn(self):
        if self._paused or not self.auto_pump:
            return False
        task_queue = self._task_queue
        if task_queue is None:
            return False
        return task_queue.run_turn()

    def _run(self):
        self._owner_ident = threading.get_ident()
        self._ready.set()
        while not self._closed.is_set():
            # Commands first: evaluate/goto/close must never queue behind
            # page work, and a command may itself be the thing that unblocks
            # a paused or parser-blocked realm.
            drained = False
            while not self._closed.is_set():
                command = self._next_command()
                if command is None:
                    break
                drained = True
                if command is _STOP:
                    self._closed.set()
                    return
                self._execute(command)
            if self._closed.is_set():
                break
            if self._run_turn():
                continue
            if drained:
                continue
            self._wake.clear()
            if self._commands.empty():
                self._wake.wait(self._idle_timeout())
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
                    if not command[0].done():
                        command[0].set_exception(RuntimeError("realm executor is closed"))
        self.wakeup()
        if not self.is_owner_thread():
            self._thread.join(timeout)
            if self._thread.is_alive():
                # A leaked owner thread is silent today.  Record it so the
                # lifecycle stress tests can assert on it.
                self._close_diagnostics.append(
                    f"{self._thread.name} did not exit within {timeout}s"
                )

    @property
    def close_diagnostics(self):
        """Teardown problems observed while stopping this executor."""
        return tuple(self._close_diagnostics)


_STOP = object()


__all__ = ["RealmExecutor", "IDLE_WAIT_MAX"]
