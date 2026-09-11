"""Registration for browser object model interfaces."""

from . import api, indexedDB, navigator, window


def register(registry):
    for module in (api, indexedDB, navigator, window):
        registry.register_namespace(vars(module))
