"""Registration for script-visible network interfaces."""

from . import websocket


def register(registry):
    registry.register_namespace(vars(websocket))
