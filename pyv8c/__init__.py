"""Public pyv8c API.

The package root deliberately loads browser bindings lazily. This keeps
metadata tooling usable on machines that only have Chromium sources and not
the optional native :mod:`pyv8` extension.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "Browser", "BrowserContext", "Page", "INTERFACES", "validate_registry", "Window",
    "Document", "Node", "Element", "HTMLElement", "Text", "Comment", "DocumentType",
    "CharacterData", "Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "EventTarget", "WindowProperties", "Location", "Navigator", "Screen", "Storage", "URL",
    "URLSearchParams",
    "NodeList", "HTMLCollection", "HTMLAllCollection", "DOMTokenList", "NamedNodeMap", "Attr", "DocumentFragment", "HTMLTemplateElement", "HTMLIFrameElement", "HTMLImageElement", "HTMLScriptElement", "HTMLLinkElement", "HTMLStyleElement",
    "Blob", "Headers", "Request", "Response", "XMLHttpRequest", "Worker", "WorkerGlobalScope", "DedicatedWorkerGlobalScope",
    "WebSocket", "MessageEvent", "CloseEvent", "ErrorEvent",
    "V8Error", "V8TypeError", "V8RangeError", "V8ReferenceError", "V8SyntaxError", "V8URIError", "V8EvalError",
    "DOMException", "DOMError", "dom_exception", "IndexSizeError", "InvalidCharacterError",
    "HierarchyRequestError", "NotFoundError", "NotSupportedError", "InvalidStateError",
    "InvalidAccessError", "SecurityError", "NetworkError", "AbortError", "TimeoutError",
    "QuotaExceededError", "DataCloneError", "as_js_error",
]

_PUBLIC_API_LOADED = False


def _load_public_api():
    global _PUBLIC_API_LOADED
    if _PUBLIC_API_LOADED:
        return

    modules = (
        ".runtime", ".bindings.registry", ".dom", ".bom", ".network", ".core.errors",
    )
    for module_name in modules:
        module = import_module(module_name, __name__)
        for name in __all__:
            if hasattr(module, name):
                globals()[name] = getattr(module, name)

    import_module(".runtime.bootstrap", __name__).bootstrap_interfaces()
    _PUBLIC_API_LOADED = True


def __getattr__(name):
    if name in __all__:
        _load_public_api()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
