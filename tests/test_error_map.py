"""One table maps a Python error name to a V8 error, read by both directions."""

import contextlib
import io

import pytest

import pyv8


class Window:
    pass


def mapped(name):
    """An error class that names itself the way the host's map does."""
    return type("Mapped", (Exception,), {"__v8_error__": True, "__v8_error_name__": name})


@pytest.mark.parametrize(
    "python_name, javascript_name",
    [
        ("V8Error", "Error"),
        ("Error", "Error"),
        ("V8TypeError", "TypeError"),
        ("TypeError", "TypeError"),
        ("V8RangeError", "RangeError"),
        ("RangeError", "RangeError"),
        ("V8ReferenceError", "ReferenceError"),
        ("ReferenceError", "ReferenceError"),
        ("V8SyntaxError", "SyntaxError"),
        ("SyntaxError", "SyntaxError"),
    ],
)
def test_every_name_the_table_declares(python_name, javascript_name):
    error = mapped(python_name)
    context = pyv8.Context(Window())

    def boom():
        raise error("message")

    context.expose(boom=boom)
    try:
        assert context.exec_js(
            "(function(){try{boom()}catch(e){return e.name+'|'+e.message}})()"
        ) == javascript_name + "|message"
    finally:
        context.close()


def test_a_bare_name_without_the_attribute_is_not_mapped():
    unmapped = type("TypeError", (Exception,), {})

    context = pyv8.Context(Window())

    def boom():
        raise unmapped("not mapped")

    context.expose(boom=boom)
    try:
        # It round-trips as the Python exception itself, not as a V8 TypeError.
        with pytest.raises(Exception, match="not mapped") as raised:
            context.exec_js("boom()")
        assert isinstance(raised.value, unmapped)
    finally:
        context.close()


def test_the_python_traceback_rides_on_the_error_instead_of_stderr():
    error = mapped("V8TypeError")
    context = pyv8.Context(Window())

    def boom():
        raise error("mapped")

    context.expose(boom=boom)
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            found = context.exec_js(
                "(function(){try{boom()}catch(e){return "
                "(e.pythonTraceback||'').indexOf('Mapped') >= 0}})()"
            )
        assert found is True
        assert captured.getvalue() == ""
    finally:
        context.close()
