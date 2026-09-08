"""Registration for Worker interfaces."""

from . import api


def register(registry):
    registry.register_namespace(vars(api))
