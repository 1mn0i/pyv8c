"""Browser-visible Error and DOMException implementations.

The Python exceptions in this module cross into JavaScript as native Error
objects.  Which constructor is used is decided by the exception's own name --
``__v8_error_name__`` when it declares one, and otherwise its class name with
the binding's ``V8`` prefix stripped -- so ``V8TypeError`` becomes a
``TypeError`` in JavaScript without a table here duplicating that fact.

The other direction needs the table at the bottom of this file, because a
JavaScript error reports its name and nothing else: the binding has no way to
know which Python class the host meant by "SecurityError".
"""

import v8n

class V8Error(Exception):
    """What a JavaScript error becomes when it crosses into Python.

    Every mapped error is one of these, and also the Python builtin it stands
    for: ``except V8Error`` catches a thrown ``TypeError``, a thrown
    ``ReferenceError`` and the rest, which is what a host that only wants to know
    "JavaScript refused" needs, while ``except TypeError`` keeps working for a
    host that wants the specific one.
    """

    __v8_error__ = True


class V8TypeError(TypeError, V8Error):
    __v8_error__ = True


class V8RangeError(ValueError, V8Error):
    __v8_error__ = True


class V8ReferenceError(ReferenceError, V8Error):
    __v8_error__ = True


class V8SyntaxError(SyntaxError, V8Error):
    __v8_error__ = True


class V8URIError(ValueError, V8Error):
    __v8_error__ = True


class V8EvalError(V8Error):
    __v8_error__ = True


class RealmBusyError(RuntimeError):
    """The realm owner thread did not pick up a command in time.

    Raised instead of blocking forever when a realm is wedged (paused
    inspector, runaway script, blocked teardown) so callers and test runs
    fail with a diagnosable error rather than hanging.
    """


class LegacyPropertyError(V8TypeError):
    """A legacy interceptor failure that is silent outside strict mode."""

    __v8_interceptor_failure__ = True
    __v8_error_name__ = "TypeError"

    @classmethod
    def named(cls, operation, name, interface, detail):
        """Build Chromium's named-property exception message."""
        preposition = "on" if operation == "set" else "from"
        return cls(
            f"Failed to {operation} a named property '{name}' {preposition} "
            f"'{interface}': {detail}"
        )

    @classmethod
    def indexed(cls, operation, index, interface, detail):
        """Build Chromium's indexed-property exception message."""
        preposition = "on" if operation == "set" else "from"
        return cls(
            f"Failed to {operation} an indexed property [{index}] {preposition} "
            f"'{interface}': {detail}"
        )


_DOM_CODES = {
    "IndexSizeError": 1,
    "DOMStringSizeError": 2,
    "HierarchyRequestError": 3,
    "WrongDocumentError": 4,
    "InvalidCharacterError": 5,
    "NoDataAllowedError": 6,
    "NoModificationAllowedError": 7,
    "NotFoundError": 8,
    "NotSupportedError": 9,
    "InUseAttributeError": 10,
    "InvalidStateError": 11,
    "SyntaxError": 12,
    "InvalidModificationError": 13,
    "NamespaceError": 14,
    "InvalidAccessError": 15,
    "ValidationError": 16,
    "TypeMismatchError": 17,
    "SecurityError": 18,
    "NetworkError": 19,
    "AbortError": 20,
    "URLMismatchError": 21,
    "QuotaExceededError": 22,
    "TimeoutError": 23,
    "InvalidNodeTypeError": 24,
    "DataCloneError": 25,
}


class DOMException(RuntimeError):
    """A browser-visible DOMException.

    The legacy numeric ``code`` is derived from :data:`_DOM_CODES` rather than
    duplicated as class constants; scripts read it through the ``code``
    accessor registered in ``engine.builtin_errors``.
    """

    __v8_error__ = True

    def __init__(self, message="", name="Error", *_extra, code=None, **_):
        if not isinstance(message, str) or not isinstance(name, str):
            import v8n

            from .arguments import dom_string

            message = "" if message is v8n.Undefined else dom_string(message)
            name = "Error" if name is v8n.Undefined else dom_string(name)
        self._message = message
        self._name = name
        self._code = int(_DOM_CODES.get(self._name, 0) if code is None else code)
        Exception.__init__(self, self._message)

    def get_name(self): return self._name
    def get_message(self): return self._message
    def get_code(self): return self._code

    @property
    def __v8_error_args__(self):
        """What the JavaScript DOMException constructor needs besides a message.

        Its second parameter *is* the DOM name, which is the whole reason this
        exists: the binding derives the constructor from the class name, and no
        class name can say "NotFoundError".  The legacy ``code`` follows from the
        name on the JavaScript side, so it needs no help.
        """
        return (self._name,)


class DOMError(Exception):
    __v8_error__ = True

    def __init__(self, message="", name="Error", *_extra, **_):
        if not isinstance(message, str) or not isinstance(name, str):
            import v8n

            from .arguments import dom_string

            message = "" if message is v8n.Undefined else dom_string(message)
            name = "Error" if name is v8n.Undefined else dom_string(name)
        self._message = message
        self._name = name
        Exception.__init__(self, self._message)

    def get_name(self): return self._name
    def get_message(self): return self._message

    @property
    def __v8_error_args__(self):
        """The name, as for DOMException; see the note there."""
        return (self._name,)


def dom_exception(name, message=""):
    """Create a fresh DOMException with the standard legacy code."""
    return DOMException(message=message, name=name)


def _factory(name):
    return lambda message="": dom_exception(name, message)


IndexSizeError = _factory("IndexSizeError")
InvalidCharacterError = _factory("InvalidCharacterError")
HierarchyRequestError = _factory("HierarchyRequestError")
NotFoundError = _factory("NotFoundError")
NotSupportedError = _factory("NotSupportedError")
InvalidStateError = _factory("InvalidStateError")
InvalidAccessError = _factory("InvalidAccessError")
SecurityError = _factory("SecurityError")
NetworkError = _factory("NetworkError")
AbortError = _factory("AbortError")
TimeoutError = _factory("TimeoutError")
QuotaExceededError = _factory("QuotaExceededError")
DataCloneError = _factory("DataCloneError")


def as_js_error(exc, *, default="Error"):
    """Normalize an internal exception at a JavaScript API boundary."""
    if isinstance(exc, (DOMException, DOMError, V8Error, V8TypeError, V8RangeError,
                        V8ReferenceError, V8SyntaxError, V8URIError, V8EvalError)):
        return exc
    if isinstance(exc, SyntaxError):
        return V8SyntaxError(str(exc))
    if isinstance(exc, TypeError):
        return V8TypeError(str(exc))
    if isinstance(exc, ValueError):
        return V8RangeError(str(exc))
    return V8Error(str(exc) or default)


def error_text(error):
    """The text of a thrown JavaScript value, for an error report.

    A JavaScript error answers ``message``; anything else is a value the page
    threw on purpose, so the text is whatever ``String(value)`` says.  Neither
    read is allowed to break a reporting path, because what is being reported
    is already a failure -- and it is the report, not the value, that a page
    is waiting for.
    """
    if isinstance(error, v8n.Value):
        message = None
        try:
            message = error.get("message")
        except BaseException:  # noqa: BLE001 - reporting must not raise
            pass
        if isinstance(message, str) and message:
            return message
        try:
            return error.to_string()
        except BaseException:  # noqa: BLE001 - reporting must not raise
            pass
    return str(error)


def install_error_map():
    """Teach the binding which Python class each JavaScript error name means.

    Registered once for the process, because the JavaScript names are global:
    two browsers in one process necessarily share one hierarchy.  Replacing the
    map is idempotent, so calling this again changes nothing.

    A JavaScript error carries only a name, so the map is keyed by name and the
    constructed class is the host's.  ``DOMException`` is the one entry that
    cannot be a class: its ``name`` member is the DOMException's own name
    (``SecurityError``, ``QuotaExceededError``, ...), so every one of those
    names needs an entry, and each is built by a callable that receives the name
    as well as the message.
    """
    mapping = {
        "Error": V8Error,
        "TypeError": V8TypeError,
        "RangeError": V8RangeError,
        "ReferenceError": V8ReferenceError,
        "SyntaxError": V8SyntaxError,
        "URIError": V8URIError,
        "EvalError": V8EvalError,
    }
    # DOM names last, so the more specific class wins where a name is shared:
    # JavaScript's SyntaxError and DOMException's SyntaxError are different
    # objects, and the DOM one is the one a page catches from the DOM.
    for dom_name in _DOM_CODES:
        mapping[dom_name] = _dom_exception_factory(dom_name)
    v8n.set_error_map(mapping)


def _dom_exception_factory(name):
    def build(_name, message):
        return DOMException(message=message, name=name)
    return build


__all__ = [
    "V8Error", "V8TypeError", "V8RangeError", "V8ReferenceError", "V8SyntaxError",
    "V8URIError", "V8EvalError", "DOMException", "DOMError", "dom_exception",
    "LegacyPropertyError", "RealmBusyError",
    "IndexSizeError", "InvalidCharacterError", "HierarchyRequestError", "NotFoundError",
    "NotSupportedError", "InvalidStateError", "InvalidAccessError", "SecurityError",
    "NetworkError", "AbortError", "TimeoutError", "QuotaExceededError", "DataCloneError",
    "as_js_error", "error_text", "install_error_map",
]

# Installed at import rather than from a caller: the hierarchy is pyv8c's, every
# entry in it already exists by the time this line runs, and a bind that depends
# on somebody remembering to install the map is a bind that silently reports
# every caught DOMException as a plain RuntimeError.
install_error_map()
