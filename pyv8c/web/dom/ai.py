"""``Summarizer``: the AI summarisation API, which has no model behind it here."""
import pyv8

from ...idl import interface

__all__ = ["Summarizer"]


@interface('Summarizer', None)
class Summarizer:
    """Chromium's summariser: this engine answers the availability question only."""

    @staticmethod
    def fn_availability(_realm, options=None, *_extra):
        """Resolved ``"unavailable"``, which is what Chromium answers without a model."""
        services = getattr(_realm, "realm_services", None)
        if services is None: return pyv8.Undefined
        promise, resolve, _reject = services.make_promise()
        resolve("unavailable")
        return promise
