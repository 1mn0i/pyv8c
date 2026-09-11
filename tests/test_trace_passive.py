"""Trace observes native boundaries without re-executing JavaScript operations."""

import pytest
import pyv8
from loguru import logger
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from tests.test_trace_contracts import Window, Target, Failure


class JsTarget:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="value", getter="observe_get", setter="observe_set", callback_type=1),),
        methods=(MethodSpec(name="echo", callback="observe_echo", callback_type=1),
                 MethodSpec(name="fail", callback="observe_fail", callback_type=1)),
    )


SETUP = """
    globalThis.reads = 0;
    globalThis.writes = 0;
    globalThis.calls = 0;
    globalThis.probes = 0;
    globalThis.stored = 5;
    globalThis.failure = new Error('original');
    faker.observe_get = function() { reads++; return stored; };
    faker.observe_set = function(value) { writes++; stored = value; };
    faker.observe_echo = function(value) { calls++; return value; };
    faker.observe_fail = function() { calls++; throw failure; };
"""


@pytest.mark.parametrize("enabled", [False, True])
def test_javascript_bindings_execute_once_with_original_identity(enabled):
    events = []
    context = pyv8.Context(Window(), trace=enabled, traceable=True,
                           trace_callback=lambda *event: events.append(event))
    try:
        target = JsTarget()
        context.expose(target=target)
        context.exec_js(SETUP)
        events.clear()
        assert context.exec_js("[target.value, target.value=9, target.echo(7), reads, writes, calls]") == [5, 9, 7, 1, 1, 1]
        assert context.exec_js("try { target.fail(); false } catch(error) { error === failure }") is True
        assert context.exec_js("calls") == 2
        if enabled:
            assert [(event[0], event[2]) for event in events] == [
                ("get", "value"), ("set", "value"), ("method", "echo"), ("method", "fail")]
            assert events[2][3:5] == ((7,), 7)
            assert events[-1][4] is pyv8.Undefined
            assert all(event[1] is target for event in events)
        else:
            assert events == []
    finally:
        context.close()


@pytest.fixture
def trace_messages():
    """Observe the actual default sink without replacing application handlers."""
    messages = []
    sink_id = logger.add(
        lambda message: messages.append(message.record["message"]),
        level="DEBUG",
        filter=lambda record: record["name"] == "pyv8.trace",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@pytest.mark.parametrize("sink_kind", ["collect", "default"])
def test_observation_never_reads_or_stringifies_js_objects(sink_kind, trace_messages):
    events = []
    callback = (lambda *event: events.append(event)) if sink_kind == "collect" else None
    context = pyv8.Context(Window(), trace=True, trace_callback=callback)
    try:
        context.expose(target=JsTarget())
        context.exec_js(SETUP)
        assert context.exec_js("""(() => {
            const object = {
                get value() { probes++; throw 1; },
                toString() { probes++; throw 2; },
                [Symbol.toPrimitive]() { probes++; throw 3; }
            };
            const array = [];
            Object.defineProperty(array, '0', {get() { probes++; throw 4; }});
            const proxy = new Proxy(object, {
                get() { probes++; throw 5; },
                ownKeys() { probes++; throw 6; },
                getOwnPropertyDescriptor() { probes++; throw 7; },
                getPrototypeOf() { probes++; throw 8; }
            });
            const revocable = Proxy.revocable({}, {});
            revocable.revoke();
            return [object, array, proxy, revocable.proxy].every(value => {
                stored = value;
                const got = target.value;
                target.value = value;
                return got === value && target.echo(value) === value;
            }) && probes === 0;
        })()""") is True
        assert context.exec_js("[reads,writes,calls,probes]") == [4, 4, 4, 0]
        if sink_kind == "collect":
            assert len(events) == 12
            assert all(isinstance(event[4], pyv8.JSObject) for event in events)
            assert trace_messages == []
        else:
            assert trace_messages == [
                "[Top] getter: JsTarget.value -> <JSObject>",
                "[Top] setter: JsTarget.value = <JSObject>",
                "[Top] method: JsTarget.echo(<JSObject>) -> <JSObject>",
            ] * 4
    finally:
        context.close()


@pytest.mark.parametrize("observer_failure", ["python", "javascript", "return"])
def test_observer_exceptions_do_not_replace_original_js_errors(observer_failure):
    context = None
    events = []

    def sink(*event):
        events.append(event)
        if observer_failure == "return":
            return 999
        if observer_failure == "python":
            raise Failure("observer failure")
        context.exec_js("throw new Error('observer failure')")

    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    try:
        context.expose(target=JsTarget())
        context.exec_js(SETUP)
        assert context.exec_js("target.echo(42)") == 42
        assert context.exec_js("try { target.fail(); false } catch(error) { error === failure }") is True
        assert context.exec_js("target.value") == 5
        assert len(events) == 3
    finally:
        context.close()


@pytest.mark.parametrize("kind,source", [
    ("get", "target.value"), ("set", "target.value=7"),
    ("method", "target.add()"), ("construct", "new Target(true)"),
])
def test_observer_failure_preserves_python_business_error(kind, source):
    def sink(*event):
        raise Failure("observer failure")

    context = pyv8.Context(Window(), trace=True, trace_callback=sink)
    try:
        target = Target()
        target.fail = True
        context.expose(Target, target=target)
        with pytest.raises(Failure, match=f"{kind} failed"):
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
        assert context.exec_js("[target.handled,target.handled=11,target[0],target[0]=21]") == [10, 11, 20, 21]
        assert target.calls == [("get", "handled"), ("set", "handled"), ("get", 0), ("set", 0)]
        if enabled:
            assert [(event[0], event[2], event[4]) for event in events] == [
                ("get", "handled", 10), ("set", "handled", 11), ("get", 0, 20), ("set", 0, 21)]
        events.clear()
        assert context.exec_js("target.extra=30; target.extra") == 30
        assert events == []
    finally:
        context.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_plain_js_accessors_and_descriptors_are_not_instrumented(enabled):
    events = []
    context = pyv8.Context(Window(), trace=enabled, trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=Target())
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
        })()""") == [True, 9, 1, 1, True, True]
        assert events == []
    finally:
        context.close()


def test_termination_skips_observer_and_context_recovers():
    events = []
    context = pyv8.Context(Window(), trace=True, trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=JsTarget())
        context.exec_js(SETUP)
        context.exec_js("faker.observe_echo = () => { while(true) {} }")
        with pytest.raises(pyv8.JavaScriptTerminated):
            context.exec_js("target.echo(1)", timeout=0.02)
        assert events == []
        context.exec_js("faker.observe_echo = value => value")
        assert context.exec_js("target.echo(42)") == 42
        assert len(events) == 1
    finally:
        context.close()
