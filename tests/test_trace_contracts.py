"""Native Context trace events, independent of classes and JS output helpers."""

import gc
import weakref

import pytest
import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

class Window:
    __v8_idl_spec__ = IdlSpec(global_this=True)


class Failure(pyv8.JsError):
    """A failure a realm is told about, inheriting the error it arrives as."""


class Target:
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=1, has_constructor=1,
        attributes=(AttributeSpec(name="value", getter="get_value", setter="set_value"),),
        methods=(MethodSpec(name="add", callback="add"),
                 MethodSpec(name="alias", callback="add")),
    )

    def __init__(self, fail=False, win=None):
        if fail:
            raise Failure("construct failed")
        self.value = 5
        self.reads = 0
        self.writes = 0
        self.fail = False

    def get_value(self):
        self.reads += 1
        if self.fail:
            raise Failure("get failed")
        return self.value

    def set_value(self, value):
        self.writes += 1
        if self.fail:
            raise Failure("set failed")
        self.value = value

    def add(self, left=0, right=0):
        if self.fail:
            raise Failure("method failed")
        return left + right


@pytest.fixture
def traced():
    events = []
    context = pyv8.Context(Window(), trace=True, ctx_type="Top", trace_callback=lambda *event: events.append(event))
    target = Target()
    context.expose(Target, target=target)
    events.clear()
    yield context, target, events
    context.close()


def test_probes_are_reported_without_extra_reads(traced):
    """A probe is its own event: reads stay at one getter event each."""
    context, target, events = traced
    assert context.exec_js("[typeof target.value, target.value, 'value' in target]").to_python() == ["number", 5, True]
    assert [(event[0], event[2], event[4]) for event in events if event[1] is target] == [
        ("get", "value", 5), ("get", "value", 5), ("has", "value", True),
    ]
    assert target.reads == 2


def test_existence_and_descriptor_probes_are_reported(traced):
    context, target, events = traced
    assert context.exec_js("'value' in target") is True
    assert context.exec_js("'nope' in target") is False
    # ``value`` lives on the prototype, so it has no own descriptor; ``in`` sees
    # the whole chain while a descriptor probe only answers for own properties.
    assert context.exec_js("Object.getOwnPropertyDescriptor(target, 'value') === undefined") is True
    assert context.exec_js("Object.getOwnPropertyDescriptor(target, 'nope') === undefined") is True
    assert [(event[0], event[2], event[4]) for event in events if event[1] is target] == [
        ("has", "value", True), ("has", "nope", False),
        ("descriptor", "value", False), ("descriptor", "nope", False),
    ]
    # Probing a property must not evaluate its accessor, and must not be
    # reported as a read either.
    assert [event for event in events if event[1] is target and event[0] == "get"] == []
    assert target.reads == 0
    events.clear()
    context.exec_js("target.extra = 7")
    assert context.exec_js("Object.getOwnPropertyDescriptor(target, 'extra').value") == 7
    assert ("descriptor", "extra", True) in [(event[0], event[2], event[4]) for event in events
                                             if event[1] is target]


def test_declined_reads_report_real_data_property_values(traced):
    """Native globals and expandos resolve outside the binding and are reported."""
    context, target, events = traced
    context.exec_js("globalThis.expando = 7")
    events.clear()
    assert context.exec_js("[expando, eval]")[0] == 7
    reads = {event[2]: event[4] for event in events if event[0] == "get"}
    assert reads["expando"] == 7
    assert repr(reads["eval"]) == "function eval() { [native code] }"
    assert [event for event in events if event[1] is target] == []
    events.clear()
    # A name no property provides has no value to show, so it is not reported.
    assert context.exec_js("typeof notThere") == "undefined"
    assert [event for event in events if event[2] == "notThere"] == []


def test_python_backed_function_calls_are_reported():
    """A function exposed to JS is traced like any other host operation."""
    events = []

    class Host:
        def echo(self, value):
            return value

    host = Host()

    def double(value):
        return value * 2

    context = pyv8.Context(Window(), trace=True, ctx_type="Top",
                           trace_callback=lambda *event: events.append(event))
    try:
        context.glob["echo"] = host.echo
        context.glob["double"] = double
        events.clear()
        assert context.exec_js("[echo(7), double(4)]").to_python() == [7, 8]
        assert [(event[0], event[1] is host, event[2], event[3], event[4]) for event in events
                if event[0] == "method"] == [
            ("method", True, "echo", (7,), 7),
            ("method", False, "double", (4,), 8),
        ]
    finally:
        context.close()


def test_four_events_and_single_property_evaluation(traced):
    context, target, events = traced
    assert context.exec_js("[target.value, target.value=7, target.add(2,3), target.alias(4,5)]").to_python() == [5, 7, 5, 9]
    context.exec_js("new Target()")
    own = [event for event in events if event[1] is target]
    assert [(event[0], event[2], event[4]) for event in own if event[2] == "value"] == [
        ("get", "value", 5), ("set", "value", 7),
    ]
    methods = [event for event in own if event[0] == "method"]
    assert [(event[2], event[3], event[4]) for event in methods] == [("add", (2, 3), 5), ("alias", (4, 5), 9)]
    construct = next(event for event in events if event[0] == "construct")
    assert construct[1] is Target
    assert construct[2:4] == ("Target", ())
    assert isinstance(construct[4], Target)
    assert construct[6] is True
    assert all(event[5] is pyv8.Undefined and event[7] == "Top" for event in own)
    assert target.reads == target.writes == 1
    assert not any(name.endswith("_hook__") for name in Target.__dict__)


@pytest.mark.parametrize("kind,source", [
    ("get", "target.value"), ("set", "target.value=2"),
    ("method", "target.add()"), ("construct", "new Target(true)"),
])
def test_errors_are_reported_as_the_realm_sees_them(traced, kind, source):
    """The trace carries the error JavaScript was thrown, and the script gets it back."""
    context, target, events = traced
    target.fail = True
    with pytest.raises(pyv8.JSException, match=f"{kind} failed"):
        context.exec_js(source)
    event = next(event for event in events
                 if event[0] == kind and isinstance(event[5], pyv8.JSObject))
    assert event[5].message == f"{kind} failed"
    target.fail = False
    assert context.exec_js("target.value") == 5


@pytest.mark.parametrize("kind,source", [
    ("get", "target.value"), ("set", "target.value=2"),
    ("method", "target.add()"), ("construct", "new Target()"),
])
def test_sink_errors_restore_trace_state(kind, source):
    active = False
    events = []

    def sink(*event):
        events.append(event)
        if active and event[0] == kind and event[2] in ("value", "add", "Target"):
            raise Failure("sink failed")

    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    try:
        context.expose(Target, target=Target())
        active = True
        result = context.exec_js(source)
        if kind == "construct":
            assert isinstance(result, Target)
        else:
            assert result == {"get": 5, "set": 2, "method": 0}[kind]
        active = False
        events.clear()
        context.exec_js("target.value")
        assert any(event[2] == "value" for event in events)
    finally:
        context.close()


def test_sink_reentry_is_silent_and_recovers():
    events = []
    context = None

    def sink(*event):
        events.append(event)
        if event[2] == "value":
            assert context.exec_js("target.add(2,3)") == 5

    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    try:
        context.expose(target=Target())
        events.clear()
        assert context.exec_js("target.value") == 5
        assert not any(event[0] == "method" for event in events)
        context.exec_js("target.add()")
        assert sum(event[0] == "method" for event in events) == 1
    finally:
        context.close()


def test_context_isolation_and_disabled_fast_path():
    events = [[], [], []]
    contexts = [pyv8.Context(Window(), trace=enabled, ctx_type=str(index),
                            trace_callback=lambda *event, index=index: events[index].append(event))
                for index, enabled in enumerate((True, True, False))]
    try:
        for context in contexts:
            context.expose(target=Target())
            context.exec_js("target.value")
        assert events[0] and events[1] and not events[2]
        assert all(event[7] == "0" for event in events[0])
        assert all(event[7] == "1" for event in events[1])
    finally:
        for context in contexts:
            context.close()


def test_no_javascript_trace_dispatch(traced):
    context, target, events = traced
    # The binding installs no dispatch object of its own, and one a script defines
    # for itself changes nothing: the trace is native either way.
    assert context.exec_js("typeof faker") == "undefined"
    context.exec_js("""
        globalThis.faker = {hook: false, getter: () => { throw 123 },
                            setter: () => { throw 123 }, method: () => { throw 123 },
                            construct: () => { throw 123 }};
    """)
    events.clear()
    assert context.exec_js("[target.value,target.value=8,target.add(1,2),new Target().value]").to_python() == [5,8,3,5]
    assert {event[0] for event in events} == {"get", "set", "method", "construct"}


def test_readonly_reflection_and_strict_mode(traced):
    context, _, _ = traced
    context.exec_js("Object.defineProperty(target,'locked',{value:1,writable:false})")
    assert context.exec_js("Reflect.set(target,'locked',2)") is False
    with pytest.raises(pyv8.JSException, match="TypeError"):
        context.exec_js("'use strict';target.locked=2")
    assert context.exec_js("target.locked") == 1
    context.exec_js("Reflect.set=()=>{throw 123}")
    assert context.exec_js("target.extra=9;target.extra") == 9


def test_callback_released_on_close():
    class Sink:
        def __call__(self, *event):
            pass

    sink = Sink()
    reference = weakref.ref(sink)
    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    del sink
    assert reference() is not None
    context.close()
    gc.collect()
    assert reference() is None


def test_invalid_callback_rejected():
    with pytest.raises(TypeError, match="trace_callback"):
        pyv8.Context(Window(), trace_callback=42)
