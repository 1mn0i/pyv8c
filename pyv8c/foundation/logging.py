"""The shared logger, imported on first use.

loguru costs about 75 ms to import and brings asyncio and ssl with it, while a browser
that never logs should not pay for that at startup.  ``logger`` stands in until the
first call needs the real one, so every call site reads the same.
"""

from __future__ import annotations

__all__ = ["logger"]


class _LazyLogger:
    """Forwards to ``loguru.logger``, importing it on first use."""

    def __getattr__(self, name):
        from loguru import logger

        return getattr(logger, name)


logger = _LazyLogger()
