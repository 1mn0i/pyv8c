"""Context-level observer activation and callback lifetime."""

import pytest
import pyv8

from tests.test_trace_contracts import Window, Target


@pytest.mark.parametrize("initial", [False, True])
def test_toggle_existing_and_new_objects(initial):
    events = []
    context = pyv8.Context(Window(), trace=initial, traceable=True,
                           trace_callback=lambda *event: events.append(event))
    try:
        target = Target()
        context.expose(target=target)
        for enabled in (False, True, False, True):
            context.trace = enabled
            context.expose(new_target=Target())
            events.clear()
            previous = target.value
            assert context.exec_js("[target.value,new_target.value,target.value=9,target.add(1,2)]") == [previous,5,9,3]
            assert bool(events) is enabled
            assert context.trace is enabled
            if enabled:
                assert {event[0] for event in events} == {"get", "set", "method"}
        assert context.exec_js("typeof globalThis.trace") == 'undefined'
    finally:
        context.close()


def test_toggle_without_observer_configuration_rejected():
    context = pyv8.Context(Window())
    try:
        context.trace = False
        with pytest.raises(RuntimeError, match="traceable=True"):
            context.trace = True
        assert context.trace is False
        assert context.exec_js("40+2") == 42
    finally:
        context.close()


def test_toggle_inside_sink_persists_after_dispatch():
    context = None
    events = []

    def sink(*event):
        events.append(event)
        context.trace = False

    context = pyv8.Context(Window(), traceable=True, trace_callback=sink)
    try:
        context.expose(target=Target())
        context.trace = True
        assert context.exec_js("target.value") == 5
        assert context.trace is False
        count = len(events)
        assert count == 1
        context.exec_js("target.value")
        assert len(events) == count
        context.trace = True
        context.exec_js("target.value")
        assert len(events) == count + 1
    finally:
        context.close()


def test_toggle_type_deletion_and_closed_context():
    context = pyv8.Context(Window(), traceable=True)
    with pytest.raises(TypeError, match="bool"):
        context.trace = 1
    with pytest.raises(TypeError, match="bool"):
        del context.trace
    context.close()
    with pytest.raises(RuntimeError, match="no longer active"):
        context.trace = True


def test_isolate_context_can_start_paused():
    isolate = pyv8.Isolate()
    events = []
    context = isolate.context(Window(), traceable=True, trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=Target())
        context.trace = True
        context.exec_js("target.value")
        assert events
    finally:
        context.close()
        del context
        isolate.close()
