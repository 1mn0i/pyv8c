"""The family a realm may be shown, and how a test joins it.

A class joins the family by inheriting the type that stands for the error a realm sees;
there is no table to install, nothing to undo afterwards, and no name to keep in step.
A class with a constructor of its own declares its shape on itself.
"""
import pyv8

#: The type each standard JavaScript error stands for.
STANDARD = {
    "Error": pyv8.JsError,
    "TypeError": pyv8.JsTypeError,
    "RangeError": pyv8.JsRangeError,
    "ReferenceError": pyv8.JsReferenceError,
    "SyntaxError": pyv8.JsSyntaxError,
    "URIError": pyv8.JsURIError,
    "EvalError": pyv8.JsEvalError,
}


def declared(name, *, javascript, base=None, arguments=(), fields=(), interceptor=False):
    """A class a realm shows as *javascript*, carrying the shape it declares.

    A name the family does not carry is declared as itself, on a class that inherits the
    plain error: that is what a class with a constructor of its own says.  The shape is
    one declaration -- the constructor name, the attributes its constructor takes after
    the message, and the attributes copied onto the error afterwards.
    """
    bases = (STANDARD.get(javascript, pyv8.JsError),)
    if base is not None:
        bases += (base,)
    if interceptor:
        bases += (pyv8.JsInterceptorFailure,)
    attributes = {}
    if javascript not in STANDARD or arguments or fields:
        attributes["__v8_error_shape__"] = (
            javascript,
            tuple(arguments),
            tuple(tuple(pair) for pair in fields),
        )
    return type(name, bases, attributes)
