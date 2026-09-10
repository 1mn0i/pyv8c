"""Deterministic tests for routing identity and pause mailbox transitions."""

import threading
from types import SimpleNamespace

from pyv8.devtools import DevToolsOptions
from pyv8.devtools.requests import Requests
from pyv8.devtools.scheduler import CallingThread, Mailbox


class FakeContext:
    isolate_id = 123

    def __init__(self):
        self.busy = False
        self.wake = None

    def _try_inspector(self, callback, wake):
        if self.busy:
            self.wake = wake
            return False
        callback()
        return True


def endpoint_for(mailbox):
    from pyv8.devtools.scheduler import Host
    from pyv8.devtools.session import Target
    target = Target(Host(FakeContext(), CallingThread(), lambda: "about:blank", lambda: None), DevToolsOptions())
    target.mailbox = mailbox
    return target


def test_request_ids_are_rewritten_and_scoped():
    requests = Requests()
    first, second = object(), object()
    payload = {"id": 1, "method": "Debugger.enable"}
    first_request = requests.add(first, payload)
    second_request = requests.add(second, payload)
    assert first_request["id"] != second_request["id"]
    assert requests.resolve(second, dict(first_request)) is None
    response = {"id": second_request["id"], "result": {}}
    assert requests.resolve(second, response).session is second
    assert response["id"] == 1
    requests.cancel(first)
    assert requests.resolve(first, dict(first_request)) is None
    assert requests.pending == {}


def test_paused_mailbox_dispatches_all_targets_and_supports_nested_waits():
    mailbox = Mailbox()
    first = SimpleNamespace(host=SimpleNamespace(executor=CallingThread()))
    second = SimpleNamespace(host=SimpleNamespace(executor=CallingThread()))
    seen = []

    def nested():
        mailbox.submit(second, lambda: (seen.append("nested"), mailbox.quit_pause()))
        mailbox.run_pause()
        mailbox.quit_pause()

    mailbox.submit(first, nested)
    mailbox.prepare_pause()
    mailbox.run_pause()
    assert seen == ["nested"]
    assert mailbox.pause_thread is None
    assert not mailbox.queue


def test_startup_wait_processes_mailbox_until_ready():
    mailbox = Mailbox()
    endpoint = endpoint_for(mailbox)
    ready = threading.Event()
    mailbox.submit(endpoint, ready.set)
    assert mailbox.wait(endpoint, ready.is_set, 0.1)
    assert not mailbox.wait(endpoint, lambda: False, 0)


def test_options_are_explicit_and_immutable():
    options = DevToolsOptions()
    assert options.pause_on_start
    assert not options.wait_for_debugger
    assert options.port == 0


def test_busy_isolate_leaves_work_in_mailbox_for_pause_thread():
    mailbox = Mailbox()
    endpoint = endpoint_for(mailbox)
    endpoint.host.context.busy = True
    seen = []
    future = mailbox.submit(endpoint, lambda: (seen.append("pause"), mailbox.quit_pause()))
    mailbox.drain(endpoint)
    assert not future.done()
    assert len(mailbox.queue) == 1
    assert endpoint.host.context.wake is not None
    mailbox.prepare_pause()
    mailbox.run_pause()
    assert future.done()
    assert seen == ["pause"]


def test_disposal_is_deferred_until_pause_stack_unwinds():
    mailbox = Mailbox()
    endpoint = endpoint_for(mailbox)
    seen = []
    mailbox.prepare_pause()
    dispose = mailbox.submit(endpoint, lambda: seen.append("dispose"), during_pause=False)
    mailbox.submit(endpoint, lambda: (seen.append("resume"), mailbox.quit_pause()))
    mailbox.run_pause()
    assert seen == ["resume"]
    assert not dispose.done()
    mailbox.drain(endpoint)
    assert seen == ["resume", "dispose"]


class RecordingTransport:
    def __init__(self):
        self.sent = []

    def send(self, payload, generation):
        self.sent.append((dict(payload), generation))


def test_connection_generation_drops_queued_requests_and_late_responses():
    from pyv8.devtools.router import Router
    from pyv8.devtools.scheduler import Host

    router = Router(DevToolsOptions())
    target = router.register(Host(FakeContext(), CallingThread(), lambda: "about:blank", lambda: None))
    router.transport = RecordingTransport()
    router.connect("/", 1)
    old = router.sessions[None]
    dispatched = []
    old.ensure_native = lambda: SimpleNamespace(send=lambda payload: dispatched.append(payload))
    router.dispatch('{"id":7,"method":"Runtime.enable"}', 1)
    identifier = next(iter(router.requests.pending))
    router.disconnect(1)
    router.connect("/", 2)
    router.receive(old, {"id": identifier, "result": {}})
    target.mailbox.drain(target)
    assert dispatched == []
    assert router.requests.pending == {}
    assert router.transport.sent == []
    assert router.sessions[None] is not old
    router.close()
    target.mailbox.drain(target)
    router.finish_cleanup()


def test_autoattach_parent_scope_and_actual_wait_state():
    from pyv8.devtools.router import Router
    from pyv8.devtools.scheduler import Host

    router = Router(DevToolsOptions(pause_on_start=False))
    host = lambda: Host(FakeContext(), CallingThread(), lambda: "about:blank", lambda: None)
    root = router.register(host())
    existing = router.register(host(), root, started=True)
    router.transport = RecordingTransport()
    router.connect("/", 1)
    parent = router.sessions[None]
    router._target_command("Target.setAutoAttach", {"flatten": True, "autoAttach": True, "waitForDebuggerOnStart": True}, parent)
    assert not existing.waiting
    child = router.register(host(), root)
    assert child.waiting
    grandchild = router.register(host(), child)
    assert not grandchild.sessions
    attached = [payload for payload, _ in router.transport.sent if payload.get("method") == "Target.attachedToTarget"]
    assert [event["params"]["waitingForDebugger"] for event in attached] == [False, True]
    router._target_command("Target.setAutoAttach", {"flatten": True, "autoAttach": False}, parent)
    assert not child.waiting
    router.close()
    for target in [root, existing, child, grandchild]:
        target.mailbox.drain(target)
    router.finish_cleanup()
