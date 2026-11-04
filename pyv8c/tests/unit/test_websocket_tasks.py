"""WebSocket events belong to networking, including handshake failures."""
from collections import deque
import gc
import os
import queue
import socket as sockets
import weakref
from threading import Event, RLock, Thread, get_ident
from types import SimpleNamespace

import pytest

from pyv8c.foundation.tasks import TaskSource
from pyv8c.network.websockets import _Socket, WebSocketsTransport
from pyv8c.network.cronet import CronetTransport
from pyv8c.network.socket_lifetime import OwnedSocket


class RecordingTasks:
    def __init__(self):
        self.pending = deque()

    def post_task(self, callback, source=TaskSource.DOM, **kwargs):
        self.pending.append((source, callback))

    def begin_network(self):
        pass

    def complete_network(self):
        pass


@pytest.mark.parametrize("event", ["open", "message", "error", "close"])
def test_socket_events_are_asynchronous_networking_tasks(event):
    tasks = RecordingTasks()
    delivered = []
    socket = _Socket(tasks, "ws://example.test/", [],
                     {event: lambda *args: delivered.append(args)}, {})
    socket._deliver(event, socket, "payload")
    assert delivered == []
    source, callback = tasks.pending.popleft()
    assert source is TaskSource.NETWORKING
    callback()
    assert delivered == [(socket, "payload")]
    assert not tasks.pending


def test_socket_event_delivery_preserves_fifo_without_coalescing():
    tasks = RecordingTasks()
    delivered = []
    socket = _Socket(tasks, "ws://example.test/", [], {
        name: lambda *args, name=name: delivered.append(name)
        for name in ("open", "message", "close")
    }, {})
    for name in ("open", "message", "message", "close"):
        socket._deliver(name, socket)
    assert delivered == []
    assert all(source is TaskSource.NETWORKING for source, _ in tasks.pending)
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert delivered == ["open", "message", "message", "close"]


def test_cronet_socket_events_use_the_same_networking_boundary():
    tasks = RecordingTasks()
    delivered = []
    handlers = {}
    socket = object()

    def factory(url, **kwargs):
        handlers.update(kwargs)
        return socket

    transport = object.__new__(CronetTransport)
    transport._closed = False
    transport._lock = RLock()
    transport._websockets = set()
    transport.client = SimpleNamespace(websocket=factory)
    transport.websocket(tasks, "ws://example.test/", [], {
        name: lambda *args, name=name: delivered.append(name)
        for name in ("open", "message", "error", "close")
    })
    handlers["on_open"](socket)
    handlers["on_message"](socket, "text")
    handlers["on_error"](socket, RuntimeError("failed"))
    handlers["on_close"](socket, 1006, "", False)
    assert delivered == []
    assert all(source is TaskSource.NETWORKING for source, _ in tasks.pending)
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert delivered == ["open", "message", "error", "close"]


@pytest.mark.parametrize("dispose", [False, True])
def test_close_during_handshake_closes_late_connection(monkeypatch, dispose):
    entered, release = Event(), Event()
    tasks, delivered, closed = RecordingTasks(), [], []

    class Connection:
        def close(self, code, reason):
            closed.append((code, reason))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            return iter(())

    def connect(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return Connection()

    monkeypatch.setattr("pyv8c.network.websockets._connect_client", lambda: connect)
    socket = _Socket(tasks, "ws://example.test/", [], {
        name: lambda *args, name=name: delivered.append(name)
        for name in ("open", "error", "close")
    }, {})
    socket.start()
    try:
        assert entered.wait(3)
        if dispose:
            socket.dispose()
        else:
            socket.close(1000, "cancelled")
    finally:
        release.set()
        socket._thread.join(3)
    assert not socket._thread.is_alive()
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert len(closed) == 1
    assert delivered == ([] if dispose else ["error", "close"])


def test_dispose_suppresses_already_queued_events():
    tasks, delivered = RecordingTasks(), []
    socket = _Socket(tasks, "ws://example.test/", [],
                     {"open": lambda *_: delivered.append("open")}, {})
    socket._deliver("open", socket)
    socket.dispose()
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert delivered == []


@pytest.mark.parametrize("failed", [False, True])
def test_transport_releases_finished_socket_before_close_delivery(monkeypatch, failed):
    tasks, delivered = RecordingTasks(), []

    class Connection:
        close_code, close_reason = 1000, "done"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            return iter(())

    def connect(*args, **kwargs):
        if failed:
            raise OSError("handshake failed")
        return Connection()

    monkeypatch.setattr("pyv8c.network.websockets._connect_client", lambda: connect)
    transport = WebSocketsTransport()
    socket = transport.websocket(tasks, "ws://example.test/", [], {
        name: lambda *args, name=name: delivered.append(name)
        for name in ("open", "error", "close")
    })
    socket._thread.join(3)
    assert not socket._thread.is_alive()
    assert not transport._sockets
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert delivered == (["error", "close"] if failed else ["open", "close"])
    assert not socket._callbacks


def test_socket_start_failure_releases_registration(monkeypatch):
    def fail_start(self):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(_Socket, "start", fail_start)
    transport = WebSocketsTransport()
    with pytest.raises(RuntimeError, match="cannot start thread"):
        transport.websocket(RecordingTasks(), "ws://example.test/", [], {})
    assert not transport._sockets


def test_close_callback_failure_still_releases_callbacks():
    tasks = RecordingTasks()

    def fail(*args):
        raise ValueError("listener failed")

    socket = _Socket(tasks, "ws://example.test/", [], {"close": fail}, {})
    socket._deliver("close", socket, 1000, "", True)
    with pytest.raises(ValueError, match="listener failed"):
        tasks.pending.popleft()[1]()
    assert not socket._callbacks


def test_close_handshake_does_not_block_caller_and_is_started_once():
    entered, release, returned = Event(), Event(), Event()
    calls = []

    class Connection:
        def close(self, code, reason):
            calls.append((code, reason, get_ident()))
            entered.set()
            assert release.wait(3)

    socket = _Socket(RecordingTasks(), "ws://example.test/", [], {}, {})
    socket._connection = Connection()

    def close():
        socket.close(1000, "done")
        socket.close(1000, "again")
        returned.set()

    caller = Thread(target=close)
    caller.start()
    try:
        assert entered.wait(2)
        assert returned.wait(0.5), "caller is waiting for the peer's close handshake"
    finally:
        release.set()
        caller.join(3)
        worker = getattr(socket, "_io_thread", None)
        if worker is not None:
            worker.join(3)
            assert not worker.is_alive()
    assert len(calls) == 1
    assert calls[0][:2] == (1000, "done")
    assert calls[0][2] != caller.ident


@pytest.mark.parametrize('finished', [False, True])
def test_close_before_open_delivery_fails_without_open_or_message(finished):
    tasks, events = RecordingTasks(), []
    socket = _Socket(tasks, 'ws://example.test/', [], {
        'open': lambda *_: events.append('open'),
        'message': lambda *_: events.append('message'),
        'error': lambda *_: events.append('error'),
        'close': lambda _, code, reason, clean: events.append(('close', code, reason, clean)),
    }, {})
    socket._deliver('open', socket)
    socket._deliver('message', socket, 'late', True)
    if finished:
        socket._deliver('close', socket, 1000, 'remote', True)
    socket.close()
    if not finished:
        socket._deliver('close', socket, 1000, 'remote', True)
    while tasks.pending:
        tasks.pending.popleft()[1]()
        if events == ['error']:
            assert tasks.pending, 'close needs a later callback boundary'
    assert events == ['error', ('close', 1006, '', False)]
    assert not socket._callbacks


def test_pre_open_failure_callback_exception_still_schedules_close():
    tasks, events = RecordingTasks(), []

    def fail(*args):
        raise ValueError('error callback failed')

    socket = _Socket(tasks, 'ws://example.test/', [], {
        'error': fail, 'close': lambda *args: events.append('close'),
    }, {})
    socket._deliver('close', socket, 1000, '', True)
    socket.close()
    with pytest.raises(ValueError, match='error callback failed'):
        tasks.pending.popleft()[1]()
    assert len(tasks.pending) == 1
    tasks.pending.popleft()[1]()
    assert events == ['close']
    assert not socket._callbacks


def test_slow_send_returns_before_io_and_close_follows_pending_messages():
    entered, release, returned = Event(), Event(), Event()
    tasks, operations = RecordingTasks(), []

    class Connection:
        def send(self, data):
            entered.set()
            assert release.wait(3)
            operations.append(('send', data))

        def close(self, code, reason):
            operations.append(('close', code, reason))

    socket = _Socket(tasks, 'ws://example.test/', [], {}, {})
    socket._connection = Connection()
    socket._open_delivered = True

    def submit():
        socket.send('中')
        socket.send_bytes(b'ab')
        socket.close(1000, 'done')
        returned.set()

    caller = Thread(target=submit)
    caller.start()
    try:
        assert entered.wait(2)
        assert returned.wait(0.5), 'send blocked the page owner'
        assert socket.buffered_amount == 5
    finally:
        release.set()
        caller.join(3)
        worker = getattr(socket, '_io_thread', None)
        if worker is not None:
            worker.join(3)
            assert not worker.is_alive()
    assert operations == [('send', '中'), ('send', b'ab'), ('close', 1000, 'done')]
    assert socket.buffered_amount == 5, 'I/O completion must not mutate observable state mid-task'
    while tasks.pending:
        tasks.pending.popleft()[1]()
    assert socket.buffered_amount == 0


@pytest.mark.parametrize('failure', ['send', 'post'])
def test_writer_failure_closes_connection_and_keeps_unsent_byte_count(failure):
    closed = Event()
    tasks = RecordingTasks()
    if failure == 'post':
        def fail_post(*args, **kwargs):
            raise RuntimeError('queue rejected')
        tasks.post_task = fail_post

    class Connection:
        def send(self, data):
            if failure == 'send':
                raise OSError('write failed')

        def close(self, *args):
            closed.set()

    socket = _Socket(tasks, 'ws://example.test/', [], {}, {})
    socket._connection = Connection()
    socket.send('中')
    assert closed.wait(3)
    socket._io_thread.join(3)
    assert not socket._io_thread.is_alive()
    assert socket._write_error is not None
    assert socket.buffered_amount == 3


def test_peer_close_stops_idle_writer():
    sent = Event()
    socket = _Socket(RecordingTasks(), 'ws://example.test/', [], {}, {})
    socket._connection = SimpleNamespace(send=lambda _: sent.set())
    socket.send('data')
    assert sent.wait(3)
    socket._stop_writer()
    socket._io_thread.join(3)
    assert not socket._io_thread.is_alive()


def test_idle_writer_does_not_retain_last_payload():
    waiting = Event()

    class ObservedQueue(queue.SimpleQueue):
        reads = 0

        def get(self):
            self.reads += 1
            if self.reads == 2:
                waiting.set()
            return super().get()

    class Payload(bytearray):
        pass

    socket = _Socket(RecordingTasks(), 'ws://example.test/', [], {}, {})
    socket._connection = SimpleNamespace(send=lambda _: None)
    socket._writes = ObservedQueue()
    payload = Payload(b'x' * 65536)
    reference = weakref.ref(payload)
    socket.send_bytes(payload)
    del payload
    try:
        assert waiting.wait(3)
        gc.collect()
        assert reference() is None
    finally:
        socket._stop_writer()
        socket._io_thread.join(3)
        assert not socket._io_thread.is_alive()


@pytest.mark.parametrize('close_first', [False, True])
def test_dispose_interrupts_blocked_socket_write(close_first):
    sender, peer = sockets.socketpair()
    # Windows loopback may buffer the entire write despite a small SNDBUF.
    # Zero disables that buffering so the unread peer actually blocks sendall.
    sender.setsockopt(sockets.SOL_SOCKET, sockets.SO_SNDBUF, 0)
    peer.setsockopt(sockets.SOL_SOCKET, sockets.SO_RCVBUF, 4096)
    entered, finished = Event(), Event()

    class Connection:
        socket = OwnedSocket(sender)

        def send(self, data):
            entered.set()
            try:
                sender.sendall(data)
            finally:
                finished.set()

        def close(self, *args):
            sender.close()

    transport = _Socket(RecordingTasks(), 'ws://example.test/', [], {'open': lambda *_: None}, {})
    transport._connection = Connection()
    transport._open_delivered = True
    try:
        transport.send_bytes(b'x' * (8 * 1024 * 1024))
        assert entered.wait(3)
        assert not finished.wait(0.1), 'fixture did not block the OS write'
        if close_first:
            transport.close()
        transport.dispose()
        transport.dispose()
        transport._io_thread.join(2)
        assert not transport._io_thread.is_alive(), 'dispose left the writer blocked'
        assert not transport._callbacks
    finally:
        sender.close()
        peer.close()
        transport._io_thread.join(3)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle cancellation')
def test_cancel_and_close_share_socket_lifetime_lock(monkeypatch):
    entered, release, close_started, closed = Event(), Event(), Event(), Event()
    calls = []

    class Raw:
        def fileno(self):
            assert not closed.is_set()
            return 1234

        def shutdown(self, how):
            calls.append('shutdown')

        def close(self):
            calls.append('close')
            closed.set()

    def cancel(handle, overlapped):
        assert handle == 1234
        entered.set()
        assert release.wait(3)
        assert not closed.is_set()
        calls.append('cancel')
        return 1

    monkeypatch.setattr('pyv8c.network.socket_lifetime._cancel_io', cancel)
    owned = OwnedSocket(Raw())
    aborter = Thread(target=owned.abort)

    def close():
        close_started.set()
        owned.close()

    closer = Thread(target=close)
    aborter.start()
    try:
        assert entered.wait(3)
        closer.start()
        assert close_started.wait(3)
        assert not closed.wait(0.1)
    finally:
        release.set()
        aborter.join(3)
        if closer.ident is not None:
            closer.join(3)
    assert not aborter.is_alive()
    assert not closer.is_alive()
    assert calls[:3] == ['cancel', 'shutdown', 'close']


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle cancellation')
@pytest.mark.parametrize('code', [5, 1168])
def test_cancel_failure_is_reported_after_socket_cleanup(monkeypatch, code):
    import ctypes

    closed = Event()

    class Raw:
        def fileno(self):
            return 1234

        def shutdown(self, how):
            pass

        def close(self):
            closed.set()

    def fail(*args):
        ctypes.set_last_error(code)
        return 0

    previous = ctypes.get_last_error()
    monkeypatch.setattr('pyv8c.network.socket_lifetime._cancel_io', fail)
    try:
        if code == 1168:
            OwnedSocket(Raw()).abort()  # No pending operation is already complete.
        else:
            with pytest.raises(OSError) as error:
                OwnedSocket(Raw()).abort()
            assert error.value.winerror == code
        assert closed.is_set()
    finally:
        ctypes.set_last_error(previous)
