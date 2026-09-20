"""`live_v8_handles_` has to count every handle an object really holds."""

import gc

import pyv8


class Window:
    pass


def assert_close_blocked(isolate):
    try:
        isolate.close()
    except RuntimeError as error:
        assert "persistent handle" in str(error), error
    else:
        raise AssertionError("the isolate closed while handles were live")


def test_a_bound_method_keeps_its_receiver_handle():
    isolate = pyv8.Isolate()
    context = isolate.context(Window)
    bound = context.exec_js("({ method() { return 1 } })").method
    del context
    gc.collect()
    assert_close_blocked(isolate)
    del bound
    gc.collect()
    assert isolate.close()


def test_a_python_function_template_is_released_with_its_realm():
    isolate = pyv8.Isolate()
    context = isolate.context(Window)

    def helper():
        return 5

    context.expose(helper=helper)
    assert context.exec_js("helper()") == 5
    del context
    gc.collect()
    assert isolate.close()


def test_a_host_object_pair_is_released_with_its_realm():
    isolate = pyv8.Isolate()
    context = isolate.context(Window)

    class Payload:
        pass

    context.expose(payload=Payload())
    assert context.exec_js("typeof payload") == "object"
    del context
    gc.collect()
    assert isolate.close()


def test_a_script_handle_is_released_with_its_realm():
    isolate = pyv8.Isolate()
    context = isolate.context(Window)
    script = pyv8.Script(context, "40 + 2")
    assert context.exec_js(script) == 42
    del context, script
    gc.collect()
    assert isolate.close()
