"""Small reusable helpers for generated callback wrappers."""

from __future__ import annotations

import inspect


class SignatureCache:
    """Cache Python callback parameter names for one interface universe."""

    def __init__(self):
        self._values = {}

    def names(self, callback):
        names = self._values.get(callback)
        if names is None:
            names = tuple(inspect.signature(callback).parameters)
            self._values[callback] = names
        return names

