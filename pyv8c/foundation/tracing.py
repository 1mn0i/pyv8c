"""The trace switch for host work that is not page behaviour.

A realm's sink reports what a *page* does; turning an event into a log line is the
engine layer's business.  Assembling the realm is the host's own work --
installing the Window aliases, writing the window's name, handing over
``window.chrome`` -- and Chromium reports nothing for its own extras and interface
installation either, so neither may these.

The distinction is a boundary around host work and not a mode: a probe a *script*
makes is traced, which is what ``tests/integration/test_trace.py`` pins down on both
sides.
"""

from contextlib import contextmanager

__all__ = ["silent_trace"]


@contextmanager
def silent_trace(context):
    """Run host work with the realm's trace switch off.

    A realm that was not tracing is left alone, and the previous setting is restored
    however the block ends, so work that raises inside it still leaves tracing as it
    found it.
    """
    previous = getattr(context, "trace", False)
    if not previous:
        yield
        return
    context.trace = False
    try:
        yield
    finally:
        context.trace = previous
