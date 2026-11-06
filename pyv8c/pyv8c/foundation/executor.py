"""Single-owner execution loop for a V8 realm.

One thread owns the realm, so callers submit work and may wait for it; idle time
turns the realm's event loop, and an asyncio loop on the same thread sleeps until a
command, a delivery or a deadline wakes it.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
import math
import itertools
from dataclasses import dataclass
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

from .logging import logger

from . import timer_resolution
from .host_errors import RealmBusyError
from .error_policy import control_exceptions

#: Exceptions that must interrupt the process rather than be logged.
_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit)

#: A queued command: the optional result Future, then callable, args and kwargs.
@dataclass(slots=True)
class Command:
    future: Future | None
    callback: Callable
    args: tuple
    kwargs: dict
    submitted: float
    deadline: float | None
    identity: int
    owner: object = None
    started: bool = False
    ticket: object = None
    discarded: bool = False
    owns_checkpoint: bool = False



def _default_call_timeout():
    """Seconds a synchronous ``call()`` may wait before raising.

    ``PYV8C_CALL_TIMEOUT`` overrides it; ``0`` or a negative value disables the
    bound.  The default sits far above every documented operation, so it only
    trips on a genuinely wedged realm.
    """
    raw = os.environ.get("PYV8C_CALL_TIMEOUT")
    if raw is None:
        return 60.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 60.0
    return None if value <= 0 else value


_UNSET = object()


def _report_late(future, name, submitted, timeout):
    """What a command that outlived its caller ended with.

    The caller is gone, so the answer has nowhere to go but the log: without this a
    network timeout or a failure inside a command is lost, which is what makes a
    realm that is merely waiting look like one that is silent.

    The caller's own budget is repeated here because the two durations read
    differently: ending just past it means the command only started near the
    deadline, so what took the time was whatever held the owner thread before it.
    """
    seconds = time.perf_counter() - submitted
    note = f" after it was submitted (its caller waited {timeout:g}s)"
    if future.cancelled():
        logger.warning(f"{name!r} was cancelled{note}")
        return
    outcome = future.exception()
    if outcome is None:
        logger.warning(f"{name!r} finished {seconds:.1f}s{note}")
        return
    logger.warning(f"{name!r} failed {seconds:.1f}s{note}: {type(outcome).__name__}: {outcome}")


class RealmExecutor:
    """Run all V8 and realm tasks on one dedicated thread."""

    def __init__(self, name="pyv8c-realm", *, auto_pump=True, default_timeout=_UNSET):
        self._commands: queue.SimpleQueue[Command] = queue.SimpleQueue()
        self._pending_commands = {}
        self._closed = threading.Event()
        # Serialises a submit against close, so a command cannot land in a stopped queue.
        self._submit_lock = threading.RLock()
        # Rouses an idle park; _timed also asks the loop to break a deadline wait.
        self._wake = threading.Event()
        self._timed = False
        self._owner_ident = None
        self._loop = None
        self._task_queues = {}
        self._queue_cursor = 0
        self._rendering = None
        self._foreground = None
        self._command_route_state = (None, None, False)
        self.auto_pump = bool(auto_pump)
        self.default_timeout = _default_call_timeout() if default_timeout is _UNSET else default_timeout
        self._error_sink = None
        self._deadline_source = None
        self._checkpoint = None
        self._operation_deadline = None
        self._operation_identity = None
        self._identities = itertools.count(1)
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        # Start without waiting for the thread to be scheduled: the command queue
        # is unbounded, while blocking here deadlocks two realms sharing the GIL.
        self._thread.start()

    @property
    def closed(self):
        return self._closed.is_set()

    def is_owner_thread(self):
        return threading.get_ident() == self._owner_ident

    def bind_command_queue(self, queue, runner=None, *, owns_checkpoint=False):
        """Route ordinary host operations to an owner-installed source queue.

        Explicit _task_lane=None remains the restricted bootstrap/control path.
        owns_checkpoint means the runner completes callback cleanup, including
        exceptional exits, before returning; raw controls keep the fallback.
        """
        if not self.is_owner_thread():
            raise RuntimeError('command queue binding requires the owner thread')
        if owns_checkpoint and runner is None:
            raise ValueError('checkpoint ownership requires an execution runner')
        self._command_route_state = (queue, runner, owns_checkpoint)

    def _command_route(self, callback, args, lane):
        owns_checkpoint = False
        if lane is _UNSET:
            lane, runner, owns_checkpoint = self._command_route_state
            if runner is not None:
                callback, args = runner, (callback, *args)
        return callback, args, lane, owns_checkpoint

    def attach_task_queue(self, task_queue, *, owns_checkpoint=False):
        """Attach an owner queue; opt out of fallback only if its callbacks own cleanup."""
        self._task_queues[task_queue] = owns_checkpoint
        # The queue is the realm's single reporting entry point for exceptions
        # raised by a fire-and-forget callback.
        if self._error_sink is None:
            self._error_sink = task_queue.report_error
        # Only the execution driver owns sleeping; queues merely wake it.
        task_queue.set_wakeup(self.wakeup)
        self.wakeup()

    def attach_deadline_source(self, source):
        """Attach the realm's deadline holder (its native V8Context).

        Arming it for one command is what lets a command already running be
        preempted, so a wedged realm raises :class:`pyv8.JavaScriptTerminated`
        instead of hanging the caller.
        """
        self._deadline_source = source

    def attach_checkpoint(self, checkpoint):
        self._checkpoint = checkpoint

    def attach_rendering(self, rendering):
        if not self.is_owner_thread():
            raise RuntimeError('rendering binding requires the owner thread')
        self._rendering = rendering
        rendering.set_wakeup(self.wakeup)
        self.wakeup()

    def attach_foreground(self, source):
        """Attach an owner-thread foreground task source to the driver.

        The source supplies ``has_runnable_tasks``, ``next_deadline``,
        ``run_turn`` and ``set_wakeup`` with the same contracts as task queues.
        Posting may happen on other threads; running and detaching stay on the
        owner thread.  One foreground turn is interleaved with other work.
        """
        if not self.is_owner_thread():
            raise RuntimeError('foreground binding requires the owner thread')
        if self._foreground is not None:
            raise RuntimeError('a foreground source is already bound')
        source.set_wakeup(self.wakeup)
        self._foreground = source
        self.wakeup()

    def detach_foreground(self, source):
        if not self.is_owner_thread():
            raise RuntimeError('foreground unbinding requires the owner thread')
        if self._foreground is source:
            self._foreground = None
            source.set_wakeup(None)

    def detach_deadline_source(self, source):
        if self._deadline_source is source:
            self._deadline_source = None

    def wakeup(self):
        """Rouse a sleeping owner thread: the event for an idle park, the loop for a deadline."""
        self._wake.set()
        if not self._timed:
            # The single-attribute read may be stale, and then the park's own re-check
            # sees the event instead: the flag only avoids the loop hop.
            return
        loop = self._loop
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # The loop is closed: the thread is gone or on its way out.
            pass

    def call(self, callback, *args, timeout=_UNSET, _task_lane=_UNSET, **kwargs):
        """Run *callback* on the owner thread and return or raise its result.

        Raises :class:`RealmBusyError` when the owner thread does not pick the
        command up within *timeout*: a wedged realm surfaces as an error.
        """
        if timeout is _UNSET:
            if self.is_owner_thread():
                return callback(*args, **kwargs)
            timeout = self.default_timeout
        if timeout is not None:
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError('timeout must be finite and non-negative, or None')
        submitted = time.perf_counter()
        deadline = None if timeout is None else submitted + timeout
        if self.is_owner_thread():
            if deadline is None:
                return callback(*args, **kwargs)
            previous = self._operation_deadline, self._operation_identity
            if previous[0] is not None:
                deadline = min(deadline, previous[0])
            if deadline <= submitted:
                raise RealmBusyError('inline operation deadline expired before execution')
            self._operation_deadline = deadline
            self._operation_identity = previous[1] if previous[1] is not None else next(self._identities)
            try:
                return callback(*args, **kwargs)
            finally:
                self._operation_deadline, self._operation_identity = previous
        name = getattr(callback, "__name__", callback)
        callback, args, _task_lane, owns_checkpoint = self._command_route(callback, args, _task_lane)
        future: Future[object] = Future()
        command = Command(future, callback, args, kwargs, submitted, deadline, next(self._identities),
                          owner=getattr(callback, '__self__', None), owns_checkpoint=owns_checkpoint)
        self._submit_command(command, _task_lane)
        try:
            return future.result(timeout=None if deadline is None else max(0.0, deadline-time.perf_counter()))
        except FutureTimeoutError:
            if future.done():
                return future.result()
            # A queued command cancels safely; a running one was already preempted
            # by the realm's deadline, and what it ends with is logged, not awaited.
            cancelled = future.cancel()
            if not cancelled:
                future.add_done_callback(
                    lambda done: _report_late(done, name, submitted, timeout))
            raise RealmBusyError(
                f"realm executor did not complete {name!r} within {timeout}s"
                + (" (command cancelled)" if cancelled else
                   " (already running; its outcome is logged when it ends)")
            ) from None

    def post(self, callback, *args, _task_lane=_UNSET, **kwargs):
        """Asynchronously execute a callback on the owner thread."""
        submitted = time.perf_counter()
        deadline = None if self.default_timeout is None else submitted + self.default_timeout
        callback, args, _task_lane, owns_checkpoint = self._command_route(callback, args, _task_lane)
        command = Command(None, callback, args, kwargs, submitted, deadline, next(self._identities),
                          owner=getattr(callback, '__self__', None), owns_checkpoint=owns_checkpoint)
        try:
            self._submit_command(command, _task_lane)
        except RuntimeError:
            return False
        return True

    def _submit_command(self, command, lane):
        with self._submit_lock:
            if self.closed:
                raise RuntimeError('realm executor is closed')
            self._pending_commands[command.identity] = command
            if lane is None:
                self._commands.put(command)
        if command.future is not None:
            command.future.add_done_callback(lambda future: self._cancel_queued(command) if future.cancelled() else None)
        if lane is not None:
            try:
                ticket = lane.post_managed(lambda: self._execute_routed(command))
            except BaseException:
                self._cancel_queued(command)
                raise
            with self._submit_lock:
                command.ticket = ticket
                pending = command.identity in self._pending_commands
            if ticket is None:
                self._fail_commands((command,), 'document task queue is closed')
                raise RuntimeError('document task queue is closed')
            if not pending:
                ticket.cancel()
        self.wakeup()

    def _cancel_queued(self, command):
        with self._submit_lock:
            if command.started:
                return
            self._pending_commands.pop(command.identity, None)
            ticket = command.ticket
            command.discarded = True
            retired = command.callback, command.args, command.kwargs
            command.callback, command.args, command.kwargs = None, (), {}
        if ticket is not None:
            ticket.cancel()
        del retired  # Finalizers may reenter the executor; never run under its lock.

    def cancel_owner(self, owner):
        """Fail commands belonging to a closing document without stopping siblings."""
        with self._submit_lock:
            commands = tuple(command for command in self._pending_commands.values() if command.owner is owner)
            for command in commands:
                self._pending_commands.pop(command.identity, None)
        self._fail_commands(commands, 'window executor is closed')

    def _execute_routed(self, command):
        with self._submit_lock:
            # Leave closed submissions registered for the close path to settle.
            if self.closed:
                return
            self._pending_commands.pop(command.identity, None)
            if command.discarded or (command.future is not None and command.future.done()):
                return
            if command.future is not None and not command.future.set_running_or_notify_cancel():
                return
            command.started = True
        self._execute(command)

    def _next_command(self):
        with self._submit_lock:
            while not self.closed:
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    return None
                self._pending_commands.pop(command.identity, None)
                if command.discarded:
                    continue
                if command.future is not None and (command.future.done() or not command.future.set_running_or_notify_cancel()):
                    continue
                command.started = True
                return command
        return None

    def has_pending_for(self, owners):
        with self._submit_lock:
            return any(any(command.owner is owner for owner in owners) and (command.future is None or not command.future.cancelled())
                       for command in self._pending_commands.values())

    def _execute(self, command):
        future, callback, args, kwargs = command.future, command.callback, command.args, command.kwargs
        if not command.started and future is not None and not future.set_running_or_notify_cancel():
            # The caller timed out while this command waited in the queue.
            return
        source = self._deadline_source
        previous_operation = self._operation_deadline, self._operation_identity
        self._operation_deadline = command.deadline
        self._operation_identity = command.identity
        budget = self.remaining_budget()
        armed = source is not None and bool(budget)
        if armed:
            try:
                source.set_deadline(budget)
            except BaseException:
                # The context may already be gone, which leaves nothing to preempt;
                # the command still has work to do, cleanup above all.
                armed = False
        try:
            if command.deadline is not None and budget <= 0:
                raise RealmBusyError("operation deadline expired before execution")
            result = callback(*args, **kwargs)
            if self._checkpoint is not None and not command.owns_checkpoint:
                self._checkpoint()
        except BaseException as exc:
            if future is None:
                # post() carries no Future, so the exception has nowhere to go.
                self._report_exception(exc)
                return
            future.set_exception(exc)
        else:
            if future is not None:
                future.set_result(result)
        finally:
            if armed:
                try:
                    source.clear_deadline()
                except BaseException:
                    # The command may have closed the context it borrowed.
                    pass
            self._operation_deadline, self._operation_identity = previous_operation
            command.callback, command.args, command.kwargs = None, (), {}

    def remaining_budget(self):
        deadline = self._operation_deadline
        return None if deadline is None else max(0.0, deadline - time.perf_counter())

    @property
    def has_operation(self):
        return self._operation_identity is not None

    def _report_exception(self, exc):
        """Handle an exception raised by a fire-and-forget command.

        Control exceptions are re-raised so a ``Ctrl-C`` reaching the realm
        interrupts the process instead of being written to a log.
        """
        if isinstance(exc, _CONTROL_EXCEPTIONS):
            raise exc
        if isinstance(exc, control_exceptions()):
            logger.debug("realm background operation terminated")
            return
        reported = False
        sink = self._error_sink
        if sink is not None:
            try:
                sink(exc)
                reported = True
            except control_exceptions():
                raise
            except BaseException:  # reporting must not break the loop
                reported = False
        if not reported:
            logger.opt(exception=exc).error("realm command failed")

    def detach_task_queue(self, task_queue):
        self._task_queues.pop(task_queue, None)

    def _next_deadline(self):
        if not self.auto_pump:
            return None
        deadlines = [value for queue in tuple(self._task_queues)
                     if not queue.closed and (value := queue.next_deadline()) is not None]
        if self._rendering is not None:
            value = self._rendering.next_deadline()
            if value is not None:
                deadlines.append(value)
        if self._foreground is not None:
            value = self._foreground.next_deadline()
            if value is not None:
                deadlines.append(value)
        return min(deadlines) if deadlines else None

    def _run_turn(self):
        if not self.auto_pump:
            return False
        queues = tuple(self._task_queues.items())
        if not queues:
            return False
        for offset in range(len(queues)):
            index = (self._queue_cursor + offset) % len(queues)
            tasks, owns_checkpoint = queues[index]
            if not tasks.has_runnable_tasks():
                continue
            try:
                worked = tasks.run_turn()
                if worked and self._checkpoint is not None and not owns_checkpoint:
                    self._checkpoint()
            except _CONTROL_EXCEPTIONS:
                raise
            except control_exceptions():
                # A terminated task is not a page error and must not retire the
                # owner thread shared by unrelated live realms.
                logger.debug("realm task terminated")
                worked = True
            except BaseException as exc:
                # Task cleanup and microtask checkpoints are part of this turn.
                # Report against its owner without killing unrelated queued work.
                tasks.report_error(exc)
                worked = True
            if worked:
                self._queue_cursor = (index + 1) % len(queues)
                return True
        return False

    def _has_work(self):
        """Whether anything the driver would run is already queued."""
        if not self._commands.empty():
            return True
        return self.auto_pump and (any(tasks.has_runnable_tasks()
                                      for tasks in tuple(self._task_queues))
                                  or (self._foreground is not None
                                      and self._foreground.has_runnable_tasks())
                                  or (self._rendering is not None
                                      and self._rendering.has_runnable_tasks()))

    def _sleep(self, seconds=None):
        """Sleep the owner thread until *seconds* elapse or :meth:`wakeup` is called.

        The loop's only callback stops it, so neither user code nor the pump ever
        runs inside ``run_forever``; that is what lets a command wait in here.
        """
        self._wake.clear()
        if self._has_work():
            # A delivery that raced the sleep decision is not slept out.
            return
        # A future timer is not runnable yet. Re-read its deadline after clear,
        # otherwise a wake cleared above can leave the driver parked forever.
        hint = self._next_deadline()
        if hint is not None:
            seconds = hint if seconds is None else min(seconds, hint)
        if seconds is None:
            # Nothing is due: park on the event instead of paying for the loop.
            self._wake.wait()
            return
        loop = self._loop
        self._timed = True
        handle = loop.call_later(seconds, loop.stop)
        try:
            if not self._wake.is_set():
                # Only a wait towards a deadline asks for the finer platform timer.
                with timer_resolution.hold():
                    loop.run_forever()
        finally:
            handle.cancel()
            self._timed = False

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._owner_ident = threading.get_ident()
        try:
            self._drive()
        finally:
            loop.close()
            # A thread that went away for any reason must take its callers with it:
            # a closed executor fails fast instead of blocking them until timeout.
            self._closed.set()
            self._fail_pending()

    def _drive(self):
        while not self._closed.is_set():
            self._pump()

    def _pump(self):
        """Alternate bounded command work with page work before considering sleep."""
        commanded = self._drain_commands()
        if self._closed.is_set():
            return
        worked = self._run_turn()
        foreground = False
        if (self.auto_pump and not self.closed and self._foreground is not None
                and self._foreground.has_runnable_tasks()):
            try:
                foreground = self._foreground.run_turn()
            except _CONTROL_EXCEPTIONS:
                raise
            except control_exceptions():
                logger.debug("foreground operation terminated")
                foreground = True
            except BaseException as error:
                self._report_exception(error)
                foreground = True
        rendered = False
        if self.auto_pump and not self.closed and self._rendering is not None:
            try:
                rendered = self._rendering.run_frame()
            except _CONTROL_EXCEPTIONS:
                raise
            except control_exceptions():
                logger.debug("rendering operation terminated")
                rendered = True
            except BaseException as error:
                self._report_exception(error)
                rendered = True
        if worked or foreground or rendered:
            return
        if commanded:
            return
        self._sleep()

    def _drain_commands(self):
        # A command can post its successor. Never drain that chain ahead of tasks.
        if self._closed.is_set():
            return False
        command = self._next_command()
        if command is None:
            return False
        try:
            self._execute(command)
        except _CONTROL_EXCEPTIONS:
            self._closed.set()
            raise
        return True

    def _fail_pending(self):
        """Complete the callers still waiting on commands that will not run."""
        with self._submit_lock:
            commands = tuple(self._pending_commands.values())
            self._pending_commands.clear()
            while True:
                try:
                    self._commands.get_nowait()
                except queue.Empty:
                    break
        self._fail_commands(commands, 'realm executor is closed')

    def _fail_commands(self, commands, reason):
        for command in commands:
            with self._submit_lock:
                if command.started or command.discarded:
                    continue
                command.discarded = True
                self._pending_commands.pop(command.identity, None)
                retired = command.callback, command.args, command.kwargs
                command.callback, command.args, command.kwargs = None, (), {}
            if command.ticket is not None:
                command.ticket.cancel()
            if command.future is not None and not command.future.done():
                if command.future.set_running_or_notify_cancel():
                    command.future.set_exception(RuntimeError(reason))
            del retired

    def close(self, timeout=5):
        with self._submit_lock:
            if not self.closed:
                self._closed.set()
        self._fail_pending()
        # Owner-side close only signals exit. An external close still joins an
        # already signalled thread so its last command cannot retain a realm.
        self.wakeup()
        if not self.is_owner_thread():
            self._thread.join(timeout)
            if self._thread.is_alive():
                # A leaked owner thread keeps the isolate alive; say so.
                logger.warning(f"{self._thread.name} did not exit within {timeout}s")


__all__ = ["RealmExecutor"]
