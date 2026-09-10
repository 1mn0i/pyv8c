"""Host adapters and the isolate-wide Inspector mailbox."""

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
import threading
import time
from typing import Callable, Protocol
import weakref


class Executor(Protocol):
    @property
    def closed(self) -> bool: ...
    def is_owner_thread(self) -> bool: ...
    def post(self, callback, *args) -> bool: ...


class CallingThread:
    def __init__(self):
        self.ident = threading.get_ident()
        self.closed = False

    def is_owner_thread(self):
        return threading.get_ident() == self.ident

    def post(self, callback, *args):
        return not self.closed


@dataclass
class Host:
    context: object
    executor: Executor
    url: Callable[[], str]
    close: Callable[[], None]
    kind: str = "page"


@dataclass
class Work:
    endpoint: object
    callback: Callable
    future: Future
    during_pause: bool = True

    def run(self):
        if not self.future.set_running_or_notify_cancel():
            return
        try:
            self.future.set_result(self.callback())
        except BaseException as error:
            self.future.set_exception(error)


class Mailbox:
    _instances = weakref.WeakValueDictionary()
    _registry_lock = threading.Lock()

    @classmethod
    def for_isolate(cls, isolate_id):
        with cls._registry_lock:
            mailbox = cls._instances.get(isolate_id)
            if mailbox is None:
                mailbox = cls()
                cls._instances[isolate_id] = mailbox
            return mailbox

    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.queue = deque()
        self.pause_thread = None
        self.pause_frames = []
        self.revision = 0

    def submit(self, endpoint, callback, *, during_pause=True):
        work = Work(endpoint, callback, Future(), during_pause)
        with self.condition:
            if endpoint.host.executor.closed:
                work.future.set_exception(RuntimeError("realm executor is closed"))
                return work.future
            self.queue.append(work)
            self.revision += 1
            paused = self.pause_thread is not None
            self.condition.notify_all()
        if not paused and not endpoint.host.executor.post(self.drain, endpoint):
            with self.condition:
                if work in self.queue:
                    self.queue.remove(work)
                    work.future.set_exception(RuntimeError("realm executor is closed"))
        return work.future

    def _take(self, endpoint=None):
        for work in self.queue:
            if (endpoint is None or work.endpoint is endpoint) and (self.pause_thread is None or work.during_pause):
                self.queue.remove(work)
                return work
        return None

    def drain(self, endpoint):
        while True:
            with self.condition:
                if self.pause_thread is not None:
                    return
                candidate = next((work for work in self.queue if work.endpoint is endpoint), None)
            if candidate is None:
                return
            if candidate.during_pause:
                try:
                    if not endpoint.host.context._try_inspector(
                        lambda: self._run_one(endpoint), endpoint.wake_dispatch,
                    ):
                        return
                except BaseException as error:
                    with self.condition:
                        if candidate in self.queue:
                            self.queue.remove(candidate)
                            candidate.future.set_exception(error)
            else:
                self._run_one(endpoint)

    def _run_one(self, endpoint):
        with self.condition:
            work = self._take(endpoint)
        if work is not None:
            work.run()

    def wait(self, endpoint, predicate, timeout=None):
        if not endpoint.host.executor.is_owner_thread():
            raise RuntimeError("debugger wait must run on the host thread")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self.condition:
                if predicate():
                    return True
                revision = self.revision
            self.drain(endpoint)
            with self.condition:
                if predicate():
                    return True
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                if revision == self.revision:
                    self.condition.wait(remaining)

    def prepare_pause(self):
        with self.condition:
            self.pause_thread = threading.get_ident()
            self.condition.notify_all()

    def run_pause(self):
        frame = threading.Event()
        with self.condition:
            self.pause_frames.append(frame)
        try:
            while not frame.is_set():
                with self.condition:
                    work = self._take()
                    if work is None:
                        self.condition.wait()
                        continue
                work.run()
        finally:
            with self.condition:
                self.pause_frames.remove(frame)
                if not self.pause_frames:
                    self.pause_thread = None
                    endpoints = {work.endpoint for work in self.queue}
                else:
                    endpoints = set()
                self.condition.notify_all()
            for endpoint in endpoints:
                endpoint.host.executor.post(self.drain, endpoint)

    def quit_pause(self):
        with self.condition:
            if self.pause_frames:
                self.pause_frames[-1].set()
            self.condition.notify_all()

    def wake(self):
        with self.condition:
            self.revision += 1
            self.condition.notify_all()
