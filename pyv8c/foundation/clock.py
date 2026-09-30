"""The clock performance entries are timed with: an epoch origin, and offsets from it."""

import time

#: One instant, read twice: the epoch a page can compare against ``Date.now()``, and
#: the monotonic reading the entry offsets count from.  A document resets both.
_STARTED_EPOCH = time.time() * 1000.0
_STARTED_MONOTONIC = time.perf_counter()


def time_origin():
    """The epoch milliseconds ``performance.timeOrigin`` reports."""
    return _STARTED_EPOCH


def now_ms():
    """Milliseconds since :func:`time_origin`, at the resolution the platform has."""
    return (time.perf_counter() - _STARTED_MONOTONIC) * 1000.0


def reset(epoch_ms=None):
    """Start a new timeline, as a document's navigation does."""
    global _STARTED_EPOCH, _STARTED_MONOTONIC
    _STARTED_EPOCH = time.time() * 1000.0 if epoch_ms is None else float(epoch_ms)
    _STARTED_MONOTONIC = time.perf_counter()
