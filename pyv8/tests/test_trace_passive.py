"""Trace observes native boundaries without re-executing JavaScript operations."""

import pytest
from pyv8c import pyv8
from pyv8c.pyv8 import IdlSpec

from tests.test_trace_contracts import Window, Target


@pytest.mark.parametrize("kind,source", [
    ("get", "target.value"), ("set", "target.value=7"),
    ("method", "target.add()"), ("construct", "new Target(true)"),
])
def test_observer_failure_preserves_python_business_error(kind, source):
    """The observer's own failure must not become the failure that is reported.

    The target's failure is one a realm may see, so that is what the operation ends
    with -- not the failure the observer raised while watching it.
    """
    class ObserverFailure(RuntimeError):
        """The observer's own: nothing joins it to the family."""

    def sink(*event):
        raise ObserverFailure("observer failure")

    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    try:
        target = Target()
        target.fail = True
        context.expose(Target, target=target)
        with pytest.raises(pyv8.JSException, match=f"{kind} failed"):
            context.exec_js(source)
    finally:
        context.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_business_handlers_preserve_decline_and_return_values(enabled):
    class Legacy:
        __v8_idl_spec__ = IdlSpec(name_get="get_named", name_set="set_named",
                                index_get="get_indexed", index_set="set_indexed")

        def __init__(self):
            self.calls = []
            self.named = 10
            self.indexed = 20

        def get_named(self, name):
            self.calls.append(("get", name))
            return self.named if name == "handled" else None

        def set_named(self, name, value):
            self.calls.append(("set", name))
            if name == "handled":
                self.named = value
                return True
            return None

        def get_indexed(self, index):
            self.calls.append(("get", index))
            return self.indexed if index == 0 else None

        def set_indexed(self, index, value):
            self.calls.append(("set", index))
            if index == 0:
                self.indexed = value
                return True
            return None

    events = []
    context = pyv8.Context(Window(), trace=enabled, trace_callback=lambda *event: events.append(event))
    try:
        target = Legacy()
        context.expose(target=target)
        target.calls.clear()
        events.clear()
        assert context.exec_js("[target.handled,target.handled=11,target[0],target[0]=21]").to_python() == [10, 11, 20, 21]
        assert target.calls == [("get", "handled"), ("set", "handled"), ("get", 0), ("set", 0)]
        if enabled:
            own = [(event[0], event[2], event[4]) for event in events if event[1] is target]
            assert [entry for entry in own if entry[0] in ("get", "set")] == [
                ("get", "handled", 10), ("set", "handled", 11), ("get", 0, 20), ("set", 0, 21)]
        events.clear()
        assert context.exec_js("target.extra=30; target.extra") == 30
        if enabled:
            # ``extra`` is neither handled nor declared, so the engine stores it as
            # a JavaScript property and the trace reports the value it holds.  The
            # probes the engine makes around the write are separate events.
            own = [(event[0], event[2], event[4]) for event in events if event[1] is target]
            assert [entry for entry in own if entry[0] in ("get", "set")] == [
                ("set", "extra", 30), ("get", "extra", 30)]
        else:
            assert events == []
    finally:
        context.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_plain_js_accessors_and_descriptors_are_not_instrumented(enabled):
    events = []
    context = pyv8.Context(Window(), trace=enabled, trace_callback=lambda *event: events.append(event))
    try:
        target = Target()
        context.expose(target=target)
        assert context.exec_js("""(() => {
            let reads = 0, writes = 0, stored;
            const getter = function() { reads++; return stored; };
            const setter = function(value) { writes++; stored = value; };
            Object.defineProperty(target, 'plain', {get:getter, set:setter, configurable:true});
            const receiver = Object.create(target);
            const success = Reflect.set(target, 'plain', 9, receiver);
            const value = receiver.plain;
            const descriptor = Object.getOwnPropertyDescriptor(target, 'plain');
            return [success,value,reads,writes,descriptor.get===getter,descriptor.set===setter];
        })()""").to_python() == [True, 9, 1, 1, True, True]
        # A JavaScript accessor is never resolved on behalf of the observation:
        # the counts above stay at one evaluation each and the target reports no
        # read or write.  The probes the engine makes around it are reported as
        # their own events, never as getter events.
        own = [event for event in events if event[1] is target]
        assert [event for event in own if event[0] in ("get", "set", "method")] == []
        if enabled:
            assert ("descriptor", "plain", True) in [(event[0], event[2], event[4]) for event in own]
    finally:
        context.close()
