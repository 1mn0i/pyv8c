"""Formatting for the realm trace hook.

A realm can carry one hook, and the bridge calls it for every access it
dispatches with three things: the event, the member's JavaScript name, and a
detail string the dispatcher may add.  That is deliberately less than the old
binding reported -- it had the receiver and the resulting value too -- because
the receiver and the value only exist inside the callback the hook is watching,
and fetching them to log them would run JavaScript as a side effect of being
observed.

What is left is enough to answer the question the hook exists for: which names
did this page touch, and in which way.  A host that needs more drives it from
JavaScript instead.
"""

from __future__ import annotations

from loguru import logger

#: Names whose traffic is noise: console conveniences, the intrinsics every
#: page reads while booting, and the handful of globals a bundler probes for
#: feature detection.  Suppressing them is what keeps a trace readable.
_EXCLUDED_NAMES = frozenset((
    "dir", "dirxml", "profile", "profileEnd", "clear", "table", "keys", "values",
    "debug", "undebug", "inspect", "copy", "queryObjects", "monitor", "unmonitor",
    "$", "$$", "$_", "$0", "$1", "$2", "$3", "$4", "$x", "DataView", "String", "Array",
    "Date", "Object", "window", "Symbol", "Number", "Function", "parseFloat", "parseInt",
    "Math", "BigInt", "RegExp", "console", "isNaN", "Boolean", "faker", "NaN", "Infinity",
    "Symbol.toPrimitive", "Symbol.toStringTag", "unescape", "decodeURIComponent",
))

#: The event names the bridge reports, mapped to what a reader calls them.
_LABELS = {
    "get": "getter",
    "set": "setter",
    "call": "method",
    "construct": "construct",
    "has": "has",
    "descriptor": "descriptor",
    "enumerate": "enumerate",
}


def format_trace(event, member, detail="", scope=""):
    """One line for a trace event, or ``None`` when the name is not worth one.

    ``scope`` names the realm the event came from.  It is an argument rather
    than something read from the realm because the hook is installed per realm
    and the caller already knows which one it bound.
    """
    name = member if isinstance(member, str) else str(member)
    if not name or name in _EXCLUDED_NAMES or name.startswith("_"):
        return None
    label = _LABELS.get(event, event)
    prefix = f"[{scope}] " if scope else ""
    suffix = f" {detail}" if detail else ""
    return f"{prefix}{label}: {name}{suffix}"


def log_trace(event, member, detail="", scope=""):
    """Default sink: write the formatted line through loguru at info level."""
    message = format_trace(event, member, detail, scope)
    if message is not None:
        logger.info(message)
    return message


__all__ = ["format_trace", "log_trace"]
