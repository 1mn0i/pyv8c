"""Browser, context, and page orchestration."""

from importlib import import_module

from .browser import Browser, BrowserContext, Page
from .realms import PageRealm, WorkerRealm

__all__ = ["Browser", "BrowserContext", "Page", "PageRealm", "WorkerRealm", "DevToolsHandle", "DevToolsOptions"]

# The DevTools adapter is re-exported, not imported: it reaches pyv8's DevTools
# service, which brings a websocket server and asyncio with it.  A browser that never
# debugs should not pay for that at import, so it loads on first attribute access.
_LAZY_EXPORTS = {"DevToolsHandle": ".devtools", "DevToolsOptions": ".devtools"}


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
