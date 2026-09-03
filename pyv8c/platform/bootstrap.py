"""Assemble built-in interface implementations in dependency order."""

from threading import RLock

from ..engine.registry import InterfaceRegistry


_bootstrap_lock = RLock()
_registration_functions = None
_default_registry = None


def bootstrap_interfaces(registry: InterfaceRegistry | None = None):
    """Build (or extend) a frozen registry of built-in interface implementations.

    Called with no arguments this returns a cached default registry, built
    once and frozen; passing a fresh ``InterfaceRegistry`` yields an isolated
    catalog for tests or experimental baselines.
    """
    global _default_registry, _registration_functions
    if registry is None:
        if _default_registry is not None:
            return _default_registry
        registry = InterfaceRegistry()
    if not isinstance(registry, InterfaceRegistry):
        raise TypeError("registry must be an InterfaceRegistry")
    if getattr(registry, "_pyv8c_bootstrapped", False):
        return registry
    with _bootstrap_lock:
        if getattr(registry, "_pyv8c_bootstrapped", False):
            return registry
        if _registration_functions is None:
            from ..engine.builtin_errors import register as register_errors
            from ..web.dom.registration import register as register_dom
            from ..web.fetch.registration import register as register_network
            from ..web.bom.registration import register as register_bom
            _registration_functions = (
                register_errors, register_dom, register_network, register_bom,
            )
        for register in _registration_functions:
            register(registry)
        registry.freeze()
        registry._pyv8c_bootstrapped = True
    if _default_registry is None:
        _default_registry = registry
    return registry
