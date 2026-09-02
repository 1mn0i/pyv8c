"""Registration for Fetch, network, and Worker interfaces."""

from . import blob, events, headers, request, response, xhr
from ..network import websocket
from ..workers import api as workers


def register(registry):
    for module in (headers, blob, request, response, events, xhr, websocket, workers):
        registry.register_namespace(vars(module))
