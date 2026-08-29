"""Assemble built-in interface implementations in dependency order."""

from ..bindings.registry import REGISTRY


_bootstrapped = False


def bootstrap_interfaces():
    global _bootstrapped
    if _bootstrapped:
        return REGISTRY
    from ..bindings.builtin_errors import register as register_errors
    from ..dom.registration import register as register_dom
    from ..network.registration import register as register_network
    from ..bom.registration import register as register_bom

    for register in (register_errors, register_dom, register_network, register_bom):
        register(REGISTRY)
    _bootstrapped = True
    return REGISTRY
