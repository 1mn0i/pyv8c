"""Chromium interface metadata and V8 binding support."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "INTERFACES", "IMPLEMENTATIONS", "validate_registry",
    "BaselineRepository", "BaselineSnapshot", "BindingSession", "InterfaceUniverse",
]


def __getattr__(name):
    if name in {"INTERFACES", "IMPLEMENTATIONS", "validate_registry"}:
        module = import_module(".registry", __name__)
    elif name in {"BaselineRepository", "BaselineSnapshot"}:
        module = import_module(".baseline", __name__)
    elif name in {"BindingSession", "InterfaceUniverse"}:
        module = import_module(".universe", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value
