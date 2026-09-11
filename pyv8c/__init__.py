"""Small public runtime surface for pyv8c.

Web interface implementation classes are Feature internals.  Applications use
them through JavaScript in a :class:`Browser` realm rather than importing
their Python implementation classes from this package.
"""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "Browser": ".platform", "BrowserContext": ".platform", "Page": ".platform",
    "DevToolsHandle": ".platform", "DevToolsOptions": ".platform",
    "BrowserEnvironment": ".foundation.environment", "ViewportConfig": ".foundation.environment",
    "ScreenConfig": ".foundation.environment", "GeolocationConfig": ".foundation.environment",
    "NetworkInformationConfig": ".foundation.environment", "BatteryConfig": ".foundation.environment",
    "StorageQuotaConfig": ".foundation.environment",
    "PluginConfig": ".foundation.environment", "MimeTypeConfig": ".foundation.environment",
}
for _name in (
    "V8Error", "V8TypeError", "V8RangeError", "V8ReferenceError", "V8SyntaxError", "V8URIError", "V8EvalError",
    "DOMException", "DOMError", "dom_exception", "IndexSizeError", "InvalidCharacterError", "HierarchyRequestError",
    "NotFoundError", "NotSupportedError", "InvalidStateError", "InvalidAccessError", "SecurityError", "NetworkError",
    "AbortError", "TimeoutError", "QuotaExceededError", "DataCloneError", "as_js_error",
):
    _EXPORTS[_name] = ".foundation.errors"

__all__ = tuple(_EXPORTS)


def __getattr__(name):
    try:
        module = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
