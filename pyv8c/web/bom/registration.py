"""Registration for browser object model interfaces."""

from . import api, navigator


def register(registry):
    registry.register_namespace(vars(api))
    registry.register_namespace(vars(navigator))
