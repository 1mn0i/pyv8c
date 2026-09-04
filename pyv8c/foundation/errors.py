"""Browser-visible Error and DOMException implementations.

The Python exceptions in this module are deliberately marked with
``__v8_error__``.  pyv8 uses that marker when crossing a Python callback
boundary so JavaScript observes native Error objects instead of opaque
Python exceptions.
"""

class V8Error(Exception):
    __v8_error__ = True


class V8TypeError(TypeError):
    __v8_error__ = True


class V8RangeError(ValueError):
    __v8_error__ = True


class V8ReferenceError(ReferenceError):
    __v8_error__ = True


class V8SyntaxError(SyntaxError):
    __v8_error__ = True


class V8URIError(ValueError):
    __v8_error__ = True


class V8EvalError(Exception):
    __v8_error__ = True


class RealmBusyError(RuntimeError):
    """The realm owner thread did not pick up a command in time.

    Raised instead of blocking forever when a realm is wedged (paused
    inspector, runaway script, blocked teardown) so callers and test runs
    fail with a diagnosable error rather than hanging.
    """


class TaskAbortedError(RuntimeError):
    """A queued task was dropped because its realm closed or cancelled it."""


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
    __v8_error__ = True

    INDEX_SIZE_ERR = 1
    DOMSTRING_SIZE_ERR = 2
    HIERARCHY_REQUEST_ERR = 3
    WRONG_DOCUMENT_ERR = 4
    INVALID_CHARACTER_ERR = 5
    NO_DATA_ALLOWED_ERR = 6
    NO_MODIFICATION_ALLOWED_ERR = 7
    NOT_FOUND_ERR = 8
    NOT_SUPPORTED_ERR = 9
    INUSE_ATTRIBUTE_ERR = 10
    INVALID_STATE_ERR = 11
    SYNTAX_ERR = 12
    INVALID_MODIFICATION_ERR = 13
    NAMESPACE_ERR = 14
    INVALID_ACCESS_ERR = 15
    VALIDATION_ERR = 16
    TYPE_MISMATCH_ERR = 17
    SECURITY_ERR = 18
    NETWORK_ERR = 19
    ABORT_ERR = 20
    URL_MISMATCH_ERR = 21
    QUOTA_EXCEEDED_ERR = 22
    TIMEOUT_ERR = 23
    INVALID_NODE_TYPE_ERR = 24
    DATA_CLONE_ERR = 25

    def __init__(self, message="", name="Error", code=None, **_):
        self._message = "" if message is None else str(message)
        self._name = str(name or "Error")
        self._code = int(_DOM_CODES.get(self._name, 0) if code is None else code)
        Exception.__init__(self, self._message)

    def get_name(self): return self._name
    def get_message(self): return self._message
    def get_code(self): return self._code


class DOMError(Exception):
    __v8_error__ = True

    def __init__(self, message="", name="Error", **_):
        self._message = "" if message is None else str(message)
        self._name = str(name or "Error")
        Exception.__init__(self, self._message)

    def get_name(self): return self._name
    def get_message(self): return self._message


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


__all__ = [
    "V8Error", "V8TypeError", "V8RangeError", "V8ReferenceError", "V8SyntaxError",
    "V8URIError", "V8EvalError", "DOMException", "DOMError", "dom_exception",
    "LegacyPropertyError", "RealmBusyError", "TaskAbortedError",
    "IndexSizeError", "InvalidCharacterError", "HierarchyRequestError", "NotFoundError",
    "NotSupportedError", "InvalidStateError", "InvalidAccessError", "SecurityError",
    "NetworkError", "AbortError", "TimeoutError", "QuotaExceededError", "DataCloneError",
    "as_js_error",
]
