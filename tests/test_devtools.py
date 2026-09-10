"""Native Inspector contracts, independent of WebSocket transport."""

import json
import threading

import pytest
import pyv8


class Window:
    pass


def test_busy_isolate_command_entry_is_nonblocking_and_notifies_release():
    first, second = pyv8.Context(Window()), pyv8.Context(Window())
    entered, release, woke = threading.Event(), threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(3)

    owner = threading.Thread(target=lambda: first._try_inspector(hold, lambda: None))
    owner.start()
    try:
        assert entered.wait(2)
        calls = []
        assert second._try_inspector(lambda: calls.append(True), woke.set) is False
        assert calls == []
        release.set()
        assert woke.wait(2)
        owner.join(2)
        assert second._try_inspector(lambda: calls.append(True), woke.set) is True
        assert calls == [True]
    finally:
        release.set()
        owner.join(3)
        first.close()
        second.close()


class Probe(pyv8.Debugger):
    def __init__(self, context):
        self.messages = []
        self.ready_count = 0
        self.sequence = 0
        super().__init__(context)

    def handle(self, message):
        self.messages.append(json.loads(message))

    def request(self, method, **params):
        self.sequence += 1
        self.send(json.dumps({"id": self.sequence, "method": method, "params": params}).encode())
        return next(message for message in reversed(self.messages) if message.get("id") == self.sequence)

    def on_ready(self):
        self.ready_count += 1

    def run_loop(self):
        self.resume()

    def quit_loop(self):
        pass

    @property
    def pauses(self):
        return [message["params"] for message in self.messages if message.get("method") == "Debugger.paused"]


@pytest.fixture
def inspector():
    context = pyv8.Context(Window())
    probe = Probe(context)
    probe.request("Runtime.enable")
    probe.request("Debugger.enable")
    try:
        yield context, probe
    finally:
        probe.disconnect()
        context.close()


def test_debugger_requires_context():
    with pytest.raises(TypeError):
        pyv8.Debugger()


def test_native_reentrant_close_is_reported_not_destroyed():
    context = pyv8.Context(Window())
    observed = []

    class ReentrantProbe(Probe):
        def run_loop(self):
            for callback in [self.disconnect, context.close]:
                with pytest.raises(RuntimeError, match="Cannot"):
                    callback()
                observed.append(True)
            self.resume()

    probe = ReentrantProbe(context)
    try:
        probe.request("Debugger.enable")
        probe.schedule_pause_on_next_statement("close check")
        assert context.exec_js("42") == 42
        assert observed == [True, True]
    finally:
        probe.disconnect()
        context.close()


def test_ready_is_notification_not_implicit_pause(inspector):
    context, probe = inspector
    probe.request("Runtime.evaluate", expression="'Runtime.runIfWaitingForDebugger'")
    assert probe.ready_count == 0
    probe.request("Runtime.runIfWaitingForDebugger")
    assert probe.ready_count == 1
    assert context.exec_js("40 + 2") == 42
    assert probe.pauses == []


@pytest.mark.parametrize("precompiled", [False, True])
def test_scheduled_pause_survives_compilation_and_is_once(inspector, precompiled):
    context, probe = inspector
    source = "globalThis.debugValue = 42"
    script = pyv8.Script(context, source, "example.js") if precompiled else source
    probe.schedule_pause_on_next_statement("custom startup")
    assert context.exec_js(script, filename="example.js") == 42
    assert context.exec_js("debugValue") == 42
    assert len(probe.pauses) == 1
    assert probe.pauses[0]["reason"] == "custom startup"
    assert probe.pauses[0]["callFrames"][0]["location"]["lineNumber"] == 0


def test_compile_failure_does_not_consume_pause(inspector):
    context, probe = inspector
    probe.schedule_pause_on_next_statement("retry")
    with pytest.raises(pyv8.JSException):
        context.exec_js("const =")
    context.exec_js("1 + 1")
    assert len(probe.pauses) == 1
    assert probe.pauses[0]["reason"] == "retry"


def test_cancel_and_console_do_not_consume_host_pause(inspector):
    context, probe = inspector
    probe.schedule_pause_on_next_statement("host")
    probe.request("Runtime.evaluate", expression="1 + 1")
    assert probe.pauses == []
    context.exec_js("40 + 2")
    assert len(probe.pauses) == 1
    probe.schedule_pause_on_next_statement("cancelled")
    probe.cancel_pause_on_next_statement()
    context.exec_js("3 + 3")
    assert len(probe.pauses) == 1


def test_sessions_are_independent_and_share_isolate(inspector):
    context, first = inspector
    other_context = pyv8.Context(Window())
    second = Probe(context)
    third = Probe(other_context)
    try:
        assert first.isolate_id == second.isolate_id == third.isolate_id == context.isolate_id
        for probe in (second, third):
            probe.request("Debugger.enable")
        first.request("Debugger.setBreakpointByUrl", lineNumber=0, url="target.js")
        second.request("Debugger.disable")
        context.exec_js("globalThis.value = 42", filename="target.js")
        assert len(first.pauses) == 1
        assert second.pauses == []
        assert third.pauses == []
        first.disconnect()
        second.request("Debugger.enable")
        second.schedule_pause_on_next_statement("second")
        context.exec_js("43")
        assert len(second.pauses) == 1
    finally:
        second.disconnect()
        third.disconnect()
        other_context.close()


def test_close_context_before_sessions_is_safe(inspector):
    context, probe = inspector
    context.close()
    probe.disconnect()
    with pytest.raises(RuntimeError):
        probe.request("Runtime.enable")


def test_precompiled_before_attach_is_debuggable():
    context = pyv8.Context(Window())
    script = pyv8.Script(context, "globalThis.value = 42", "precompiled.js")
    probe = Probe(context)
    try:
        probe.request("Debugger.enable")
        probe.request("Debugger.setBreakpointByUrl", lineNumber=0, url="precompiled.js")
        assert context.exec_js(script) == 42
        assert len(probe.pauses) == 1
    finally:
        probe.disconnect()
        context.close()
