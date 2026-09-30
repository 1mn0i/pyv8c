"""Context-level diagnostics, independent of browser classes and JS globals."""

from . import JSFunction, JSObject, Null, Undefined


#: An object's converted contents are a preview: one event stays one line, and a whole
#: object graph is neither readable nor bounded (a page global held 439 KB of it).
_MAX_DISPLAY = 160


_EXCLUDED_GETTERS = frozenset((
    "dir", "dirxml", "profile", "profileEnd", "clear", "table", "keys", "values",
    "debug", "undebug", "inspect", "copy", "queryObjects", "monitor", "unmonitor",
    "$", "$$", "$_", "$0", "$1", "$2", "$3", "$4", "$x", "DataView", "String", "Array",
    "Date", "Object", "window", "Symbol", "Number", "Function", "parseFloat", "parseInt",
    "Math", "BigInt", "RegExp", "console", "isNaN", "Boolean", "NaN", "Infinity",
    "Symbol.toPrimitive", "Symbol.toStringTag", "unescape", "decodeURIComponent",
    "Map", "Set", "Proxy"
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
    if isinstance(value, JSFunction):
        # A function value shows its source, such as
        # "function eval() { [native code] }".  The wrapper asks the binding for
        # the raw source, so an overridden ``toString`` is not called, and a
        # multi-line source is trimmed to keep one event on one log line.
        try:
            source = repr(value)
        except Exception:  # noqa: BLE001 - formatting must never break a trace
            source = ""
        if source:
            return source.splitlines()[0]
    if isinstance(value, JSObject):
        try:
            text = repr(value.to_python())
        except Exception:  # noqa: BLE001 - formatting must never break a trace
            return f"<{type(value).__name__}>"
        text = " ".join(text.split())
        if len(text) <= _MAX_DISPLAY:
            return text
        return text[:_MAX_DISPLAY] + "..."
    return f"<{type(value).__name__}>"


def _owner_name(receiver):
    """The name a traced access prints for its receiver.

    An interface-level member has no instance behind it: the binding labels that
    receiver with the interface's own name, which arrives here as a string.
    """
    if isinstance(receiver, type):
        return receiver.__name__
    if isinstance(receiver, str):
        return receiver
    return type(receiver).__name__


def format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Format one event; return None for excluded getter names."""
    if name in _EXCLUDED_GETTERS or isinstance(name, str) and name.startswith("_"):
        return None
    owner = _owner_name(receiver)
    if event == "get":
        message = f"[{ctx_type}] getter: {owner}.{name} -> {_display(value)}"
    elif event == "set":
        message = f"[{ctx_type}] setter: {owner}.{name} = {_display(value)}"
    elif event == "method":
        message = f"[{ctx_type}] method: {owner}.{name}{_display(arguments)} -> {_display(value)}"
    elif event == "has":
        message = f"[{ctx_type}] has: {owner}.{name} -> {_display(value)}"
    elif event == "descriptor":
        message = f"[{ctx_type}] descriptor: {owner}.{name} -> {_display(value)}"
    else:
        prefix = "new " if is_construct else ""
        message = f"[{ctx_type}] construct: {prefix}{name}{_display(arguments)} -> {_display(value)}"
    if error is not Undefined:
        message += f", error: {_display(error)}"
    return message


def log_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Default standalone pyv8 sink; configure logger to view it."""
    # Imported here so that a process which never logs never pays for loguru.
    from loguru import logger

    message = format_trace(event, receiver, name, arguments, value, error, is_construct, ctx_type)
    if message is not None:
        logger.debug(message)
