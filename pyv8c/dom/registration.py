"""Registration for DOM interfaces."""

from . import model


def register(registry):
    registry.register_namespace(vars(model))
    for implementation in model.HTML_CLASSES.values():
        registry.register(implementation)
