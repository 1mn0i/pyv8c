"""Browser host adapter for the DevTools service.

The adapter itself is written the way it was -- a router per browser context, a
target per page and per worker, and a host object that gives the scheduler the
realm and its executor.  What is missing is underneath it: the kernel has no
Inspector layer, so there is no session for a target to be registered with.

Both public names stay importable, and asking for a session refuses by name.
That is deliberate: a host that attaches DevTools should be told the capability
is absent, not handed a handle whose every method fails later and elsewhere.
"""

from v8n.devtools import UNAVAILABLE, DevToolsOptions

__all__ = ["DevToolsHandle", "DevToolsOptions"]


class DevToolsHandle:
    """A live Inspector session, which this build cannot create."""

    def __init__(self, *_args, **_kwargs):
        raise RuntimeError(UNAVAILABLE)


def start_devtools(context, page, *, options=None):
    """Attach a DevTools session to *context*'s page, or say why not."""
    raise RuntimeError(UNAVAILABLE)
