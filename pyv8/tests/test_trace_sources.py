"""Native source URL filtering happens before a trace reaches Python."""

import pytest
import pyv8

from tests.test_trace_contracts import Target, Window


def test_trace_globs_match_current_script_and_later_function_calls():
    events = []
    context = pyv8.Context(Window(), trace=True,
                           trace_scripts=("https://example.test/a.js?*",),
                           trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=Target())
        context.exec_js(pyv8.Script(context, "function fromA() { return target.value; }; fromA()",
                                     "https://example.test/a.js?v=1"))
        assert sum(event[2] == "value" for event in events) == 1
        events.clear()
        context.exec_js(pyv8.Script(context, "target.value; fromA()",
                                     "https://example.test/b.js"))
        assert sum(event[2] == "value" for event in events) == 1
        assert sum(event[2] == "target" for event in events) == 1
    finally:
        context.close()


def test_trace_source_patterns_validate_input_and_keep_unfiltered_behavior():
    for value in ((), ("",), (42,), "a.js"):
        with pytest.raises(ValueError):
            pyv8.Context(Window(), trace=True, trace_scripts=value)
    events = []
    context = pyv8.Context(Window(), trace=True,
                           trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=Target())
        assert context.exec_js("target.value") == 5
        assert any(event[2] == "value" for event in events)
    finally:
        context.close()


def test_multiple_source_patterns_and_unnamed_scripts():
    events = []
    context = pyv8.Context(Window(), trace=True,
                           trace_scripts=("https://example.test/a?.js", "https://example.test/b.js",
                                          "https://example.test/?.js"),
                           trace_callback=lambda *event: events.append(event))
    try:
        context.expose(target=Target())
        context.exec_js("target.value")
        assert events == []
        for name in ("a1.js", "b.js", "c2.js", "猫.js"):
            context.exec_js(pyv8.Script(context, "target.value", "https://example.test/" + name))
        assert sum(event[2] == "value" for event in events) == 3
    finally:
        context.close()
