"""Registration for browser object model interfaces."""

from . import api


def register(registry):
    registry.register_namespace(vars(api))
