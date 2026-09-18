"""Flag constants shared with the native batch registration API.

These are the vocabulary a host uses to describe an interface.  They mirror
V8's own enums where V8 has one, so a reader can map a declaration back to the
engine without a translation table, and they stay plain integers because the
batch descriptor they travel in is a flat array of numbers.
"""

from __future__ import annotations


class CallbackType:
    """Where a member's implementation lives."""

    kPy = 0  # a Python bound method or function
    kJs = 1  # a JavaScript patch installed by the host (for example faker)


class FlagImmutable:
    """Which prototypes are frozen after installation."""

    instance = 1 << 0
    prototype = 1 << 1


class V8Attribute:
    """v8::PropertyAttribute bits."""

    kNone = 0
    ReadOnly = 1 << 0  # not writable
    DontEnum = 1 << 1  # not enumerable
    DontDelete = 1 << 2  # not configurable


class FlagLocation:
    """Where a member is installed on the interface."""

    kInstance = 0
    kPrototype = 1
    kInterface = 2


class FlagReceiverCheck:
    """Whether the receiver is validated against the interface signature."""

    kCheck = 0
    kDoNotCheck = 1


class FlagCrossOriginCheck:
    """Whether a cross-origin access check runs before the member is reached."""

    kCheck = 0
    kDoNotCheck = 1


class FlagExposed:
    """Whether an interface is exposed as a constructor on the global."""

    kNo = 0
    kYes = 1


class FlagConstructor:
    """Which call forms an interface accepts: ``new Test()``, ``Test()``, both."""

    kDontAll = 0
    kNew = 1 << 0
    kCall = 1 << 1
    kAll = kNew | kCall


class SideEffectType:
    """v8::SideEffectType, which lets V8 hoist or relax accessor calls."""

    kHasSideEffect = 0
    kHasNoSideEffect = 1
    kHasSideEffectToReceiver = 2


class FlagArrayProto:
    """Which Array.prototype members an array-like interface inherits."""

    iterator = 1 << 0
    entries = 1 << 1
    keys = 1 << 2
    values = 1 << 3
    forEach = 1 << 4


__all__ = [
    "CallbackType",
    "FlagImmutable",
    "V8Attribute",
    "FlagLocation",
    "FlagReceiverCheck",
    "FlagCrossOriginCheck",
    "FlagExposed",
    "FlagConstructor",
    "SideEffectType",
    "FlagArrayProto",
]
