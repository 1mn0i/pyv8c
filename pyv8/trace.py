"""Context-level diagnostics, independent of browser classes and JS globals."""

from loguru import logger

from . import Null, Undefined


_EXCLUDED_GETTERS = frozenset((
    "dir", "dirxml", "profile", "profileEnd", "clear", "table", "keys", "values",
    "debug", "undebug", "inspect", "copy", "queryObjects", "monitor", "unmonitor",
    "$", "$$", "$_", "$0", "$1", "$2", "$3", "$4", "$x", "DataView", "String", "Array",
    "Date", "Object", "window", "Symbol", "Number", "Function", "parseFloat", "parseInt",
    "Math", "BigInt", "RegExp", "console", "isNaN", "Boolean", "faker", "NaN", "Infinity",
    "Symbol.toPrimitive", "Symbol.toStringTag", "unescape", "decodeURIComponent",
))


def _display(value):
    if value is Undefined:
        return "undefined"
    if value is Null:
        return "null"
    if value is None or type(value) in (bool, int, float, str):
        return repr(value)
    if type(value) is tuple:
        return "(" + ", ".join(_display(item) for item in value) + ")"
    return f"<{type(value).__name__}>"


def format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Format one event; return None for excluded getter names."""
    if name in _EXCLUDED_GETTERS or isinstance(name, str) and name.startswith("_"):
        return None
    owner = receiver.__name__ if isinstance(receiver, type) else type(receiver).__name__
    if event == "get":
        message = f"[{ctx_type}] getter: {owner}.{name} -> {_display(value)}"
    elif event == "set":
        message = f"[{ctx_type}] setter: {owner}.{name} = {_display(value)}"
    elif event == "method":
        message = f"[{ctx_type}] method: {owner}.{name}{_display(arguments)} -> {_display(value)}"
    else:
        prefix = "new " if is_construct else ""
        message = f"[{ctx_type}] construct: {prefix}{name}{_display(arguments)} -> {_display(value)}"
    if error is not Undefined:
        message += f", error: {_display(error)}"
    return message


def log_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Default standalone pyv8 sink; configure logger to view it."""
    message = format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type)
    if message is not None:
        logger.debug(message)
