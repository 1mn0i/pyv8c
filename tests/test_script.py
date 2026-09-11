"""Compiled scripts must separate compilation from execution and retain origin."""

import pytest
import pyv8


@pytest.fixture
def context():
    owner = pyv8.Context(object())
    try:
        yield owner
    finally:
        owner.close()


def test_compilation_has_no_execution_side_effects_and_script_can_be_reused(context):
    context.exec_js("globalThis.runs = 0")
    script = pyv8.Script(context, "++runs")
    assert isinstance(script, pyv8.Script)
    assert context.exec_js("runs") == 0
    assert context.exec_js(script) == 1
    assert context.exec_js(script) == 2


def test_script_retains_filename_in_runtime_error(context):
    script = pyv8.Script(context, "throw new Error('boom')", "compiled-example.js")
    with pytest.raises(pyv8.JSException) as caught:
        context.exec_js(script)
    assert caught.value.original.message == "boom"
    assert "compiled-example.js" in caught.value.original.stack
    assert context.exec_js("40 + 2") == 42


def test_failed_compilation_does_not_poison_next_script(context):
    with pytest.raises(pyv8.JSException) as caught:
        pyv8.Script(context, "const =", "invalid.js")
    assert caught.value.original.name == "SyntaxError"
    assert "invalid.js" in str(caught.value)
    assert context.exec_js(pyv8.Script(context, "6 * 7")) == 42
