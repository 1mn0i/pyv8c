"""Registration for Fetch interfaces."""

from . import blob, events, headers, request, response, xhr


def register(registry):
    for module in (headers, blob, request, response, events, xhr):
        registry.register_namespace(vars(module))
