"""Lazy public API facade for pyv8c.

Keeping this facade separate from the package marker makes the public import
surface independent from the internal foundation/engine/web/platform layout.
"""

from __future__ import annotations

from importlib import import_module

from . import __all__ as _PUBLIC_NAMES

_PUBLIC_API_LOADED = False


def _load_public_api():
    global _PUBLIC_API_LOADED
    if _PUBLIC_API_LOADED:
        return
    modules = (
        ".platform", ".engine.registry", ".web.dom", ".web.bom", ".web.fetch", ".web.network", ".web.workers",
        ".foundation.environment", ".foundation.errors",
    )
    for module_name in modules:
        module = import_module(module_name, __package__)
        for name in _PUBLIC_NAMES:
            if hasattr(module, name):
                globals()[name] = getattr(module, name)
    import_module(".platform.bootstrap", __package__).bootstrap_interfaces()
    _PUBLIC_API_LOADED = True


def __getattr__(name):
    if name in _PUBLIC_NAMES:
        _load_public_api()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_PUBLIC_NAMES)
