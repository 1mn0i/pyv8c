"""One family, joined by inheritance, answers both directions.

The binding keeps no names of its own: a class a realm may be shown inherits the type
that stands for that error, and what the realm calls it follows from the type it
inherited.  A class the family does not contain is the host's own failure -- one a realm
is never shown, because a failure of the host's own is not something a page could have
caught or handled.
"""
import contextlib
import io

import pytest

import pyv8
from tests.error_family import declared


class Window:
    pass


@pytest.mark.parametrize(
    "javascript",
    ["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError"],
)
def test_a_class_of_the_family_arrives_as_the_error_it_inherits(javascript):
    Mapped = declared("Mapped", javascript=javascript)
    context = pyv8.Context(Window())
    context.expose(boom=lambda: (_ for _ in ()).throw(Mapped("message")))
    try:
        assert context.exec_js(
            "(function(){try{boom()}catch(e){return e.name+'|'+e.message}})()"
        ) == javascript + "|message"
    finally:
        context.close()


def test_a_class_outside_the_family_is_not_something_a_realm_is_shown():
    class Unmapped(Exception):
        pass

    context = pyv8.Context(Window())
    context.expose(boom=lambda: (_ for _ in ()).throw(Unmapped("not mapped")))
    try:
        # The script cannot catch what it was never given, and the host hears the
        # exception it raised rather than a JavaScript one standing in for it.
        with pytest.raises(Unmapped, match="not mapped") as raised:
            context.exec_js("(function(){try{boom()}catch(e){return 'caught'}})()")
        assert isinstance(raised.value, Unmapped)
        assert context.exec_js("6 * 7") == 42
    finally:
        context.close()


def test_a_host_failure_is_kept_to_the_host():
    class HostOnly(RuntimeError):
        """The host's own: nothing here joins the family."""

    context = pyv8.Context(Window())
    context.expose(boom=lambda: (_ for _ in ()).throw(HostOnly("host boom")))
    try:
        with pytest.raises(HostOnly, match="host boom"):
            context.exec_js("(function(){try{boom()}catch(e){return 'caught'}})()")
        assert context.exec_js("7 * 6") == 42
    finally:
        context.close()


def test_a_constructor_argument_and_a_copied_field_travel_with_the_error():
    class Named(pyv8.JsError):
        __v8_error_shape__ = ("Error", ("_name",), (("extra", "extra"),))

        def __init__(self, message):
            super().__init__(message)
            self._name = "InvalidStateError"
            self.extra = 7

    context = pyv8.Context(Window())
    context.expose(boom=lambda: (_ for _ in ()).throw(Named("bad")))
    try:
        assert context.exec_js(
            "(function(){try{boom()}catch(e){return [e.message, e.extra]}})()"
        ).to_python() == ["bad", 7]
    finally:
        context.close()


def test_the_most_specific_answer_wins():
    """What a class inherits answers, and what it declares answers before that.

    An error inherits the family it was declared in -- so a subclass of a ``TypeError``
    is a ``TypeError`` -- and a class whose realm constructor is not one of the standard
    names says so itself.
    """

    class Base(pyv8.JsTypeError):
        pass

    class Specific(Base):
        __v8_error_shape__ = ("RangeError", (), ())

    context = pyv8.Context(Window())
    context.expose(base=lambda: (_ for _ in ()).throw(Base("base")))
    context.expose(specific=lambda: (_ for _ in ()).throw(Specific("specific")))
    try:
        assert context.exec_js(
            "(function(){const seen=[];for(const boom of [base,specific]){"
            "try{boom()}catch(e){seen.push(e.name)}}return seen})()"
        ).to_python() == ["TypeError", "RangeError"]
    finally:
        context.close()


def test_the_error_carries_no_python_internals():
    """What a realm is shown is the error, never the host's side of it.

    A traceback on the object would put the host's own frames and file paths where any
    script can read them, and a page reporting its errors would carry them out.  Where
    the failure came from is the host's to know; it stays out of the realm, and stderr
    stays quiet too because a caught error never reaches it.
    """
    Mapped = declared("Mapped", javascript="TypeError")
    context = pyv8.Context(Window())
    context.expose(boom=lambda: (_ for _ in ()).throw(Mapped("mapped")))
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            found = context.exec_js(
                "(function(){try{boom()}catch(e){"
                "const names = Object.getOwnPropertyNames(e);"
                "return [e.name, e.message, 'pythonTraceback' in e,"
                " names.filter((name) => name !== 'stack' && name !== 'message')"
                ".join(',')].join('|')}})()"
            )
        assert found == "TypeError|mapped|false|"
        assert captured.getvalue() == ""
    finally:
        context.close()
