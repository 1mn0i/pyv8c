"""Assemble built-in interface implementations in dependency order."""

from threading import RLock

from ..bindings.registry import REGISTRY, InterfaceRegistry


_bootstrap_lock = RLock()
_registration_functions = None


def bootstrap_interfaces(registry: InterfaceRegistry | None = None):
    """Register built-ins once per registry, safely under concurrent startup."""
    registry = REGISTRY if registry is None else registry
    if not isinstance(registry, InterfaceRegistry):
        raise TypeError("registry must be an InterfaceRegistry")
    if getattr(registry, "_pyv8c_bootstrapped", False):
        return registry
    with _bootstrap_lock:
        if getattr(registry, "_pyv8c_bootstrapped", False):
            return registry
        global _registration_functions
        if _registration_functions is None:
            from ..bindings.builtin_errors import register as register_errors
            from ..dom.registration import register as register_dom
            from ..network.registration import register as register_network
            from ..bom.registration import register as register_bom
            _registration_functions = (
                register_errors, register_dom, register_network, register_bom,
            )
        for register in _registration_functions:
            register(registry)
        registry._pyv8c_bootstrapped = True
    return registry
