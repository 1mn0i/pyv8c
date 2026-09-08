"""Registration for browser object model interfaces."""

from . import api, navigator, window


def register(registry):
    for module in (api, navigator, window):
        registry.register_namespace(vars(module))
