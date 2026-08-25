"""Runtime generation of Chromium interface shells.

The renderer snapshot is deliberately treated as metadata, not as generated
Python source.  Real classes registered by :mod:`pyv8c.interfaces` win over
the shells created here; shells only fill gaps in the observable API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyv8
from pyv8.flag import (
    CallbackType,
    FlagCrossOriginCheck,
    FlagConstructor,
    FlagLocation,
    FlagReceiverCheck,
    SideEffectType,
    V8Attribute,
)

from loguru import logger


_CACHE: dict[str, type] = {}
_BASELINE: dict[str, dict[str, Any]] | None = None
_STUB_LOGGING = "info"


def configure_stub_logging(level="info"):
    """Configure the shared stub logger without exposing it to JavaScript."""
    global _STUB_LOGGING
    _STUB_LOGGING = str(level or "info").lower()


def load_runtime_baseline(path=None) -> dict[str, dict[str, Any]]:
    global _BASELINE
    if _BASELINE is not None and path is None:
        return _BASELINE
    target = Path(path) if path else Path(__file__).resolve().parents[1] / "interface-baseline.json"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot load Chromium interface baseline: {target}") from exc
    data.pop("_webidl", None)
    data.pop("_report", None)
    if not isinstance(data, dict):
        raise TypeError("interface baseline must be an object")
    if path is None:
        _BASELINE = data
    return data


def reset_runtime_cache() -> None:
    """Clear generated classes; intended for tests that use a fixture baseline."""
    _CACHE.clear()


def _log(message, *args):
    if _STUB_LOGGING in ("off", "none", "disabled"):
        return
    # loguru and logging both accept a format string, but use the common
    # already-rendered form so the fallback logger behaves identically.
    try:
        logger.info(message.format(*args))
    except (AttributeError, TypeError):
        logger.info(message, *args)


def _location(value):
    return {
        "instance": FlagLocation.kInstance,
        "prototype": FlagLocation.kPrototype,
        "interface": FlagLocation.kInterface,
    }.get(value, FlagLocation.kPrototype)


def _side_effect(value):
    return {
        "HasNoSideEffect": SideEffectType.kHasNoSideEffect,
        "HasSideEffectToReceiver": SideEffectType.kHasSideEffectToReceiver,
    }.get(value, SideEffectType.kHasSideEffect)


def _receiver(value):
    return FlagReceiverCheck.kDoNotCheck if value == "DoNotCheck" else FlagReceiverCheck.kCheck


def _cross_origin(value):
    return FlagCrossOriginCheck.kDoNotCheck if value == "DoNotCheck" else FlagCrossOriginCheck.kCheck


def _property_flags(member):
    flags = str(member.get("flags") or "")
    result = 0
    if member.get("readonly") or "DontWrite" in flags:
        result |= V8Attribute.ReadOnly
    if "DontEnum" in flags:
        result |= V8Attribute.DontEnum
    if "DontDelete" in flags:
        result |= V8Attribute.DontDelete
    return result


def _public_members(spec):
    members = spec.get("members") or {}
    for location in ("instance", "prototype", "interface"):
        for member in members.get(location, ()) or ():
            if isinstance(member, dict) and member.get("name"):
                yield location, member


def _class_members(cls, attribute=False):
    configs = []
    for base in reversed(cls.__mro__):
        configs.extend(getattr(base, "__dict__", {}).get("__v8_attribute__" if attribute else "__v8_method__", ()) or ())
    return {item[0] for item in configs}


def _stub_method(interface_name, name):
    def callback(self, *args, **kwargs):
        _log("pyv8c: {}.{}() 待实现", interface_name, name)
        return pyv8.Undefined

    callback.__name__ = f"_pyv8c_stub_method_{interface_name}_{name}"
    callback.__idl_stub__ = True
    return callback


def _stub_getter(interface_name, name):
    def getter(self):
        _log("pyv8c: {}.{} getter 待实现", interface_name, name)
        return pyv8.Undefined

    getter.__name__ = f"_pyv8c_stub_get_{interface_name}_{name}"
    getter.__idl_stub__ = True
    return getter


def _stub_setter(interface_name, name):
    def setter(self, value):
        _log("pyv8c: {}.{} setter 待实现", interface_name, name)
        return None

    setter.__name__ = f"_pyv8c_stub_set_{interface_name}_{name}"
    setter.__idl_stub__ = True
    return setter


def _stub_constructor(interface_name):
    def constructor(self, *args, **kwargs):
        _log("pyv8c: {} constructor 待实现", interface_name)

    constructor.__name__ = interface_name
    constructor.__idl_stub__ = True
    return constructor


def _append_operation(cls, interface_name, location, member):
    name = member["name"]
    callback_name = f"_pyv8c_stub_method_{interface_name}_{name}"
    setattr(cls, callback_name, _stub_method(interface_name, name))
    config = (
        name, callback_name, int(member.get("length") or 0), CallbackType.kPy,
        _location(location), _property_flags(member), _receiver(member.get("receiver")),
        _cross_origin(member.get("cross_origin")), _side_effect(member.get("side_effect")),
    )
    old = list(cls.__dict__.get("__v8_method__", ()) or ())
    old.append(config)
    setattr(cls, "__v8_method__", tuple(old))


def _append_attribute(cls, interface_name, location, member):
    name = member["name"]
    getter_name = f"_pyv8c_stub_get_{interface_name}_{name}"
    setter_name = None
    setattr(cls, getter_name, _stub_getter(interface_name, name))
    if member.get("setter") and not member.get("readonly"):
        setter_name = f"_pyv8c_stub_set_{interface_name}_{name}"
        setattr(cls, setter_name, _stub_setter(interface_name, name))
    config = (
        name, getter_name, setter_name, CallbackType.kPy, _location(location),
        _property_flags(member), _receiver(member.get("receiver")),
        _cross_origin(member.get("cross_origin")), _cross_origin(member.get("cross_origin")),
        _side_effect(member.get("side_effect")),
    )
    old = list(cls.__dict__.get("__v8_attribute__", ()) or ())
    old.append(config)
    setattr(cls, "__v8_attribute__", tuple(old))


def _install_missing_members(cls, name, spec):
    methods = _class_members(cls)
    attributes = _class_members(cls, attribute=True)
    for location, member in _public_members(spec):
        member_name = member["name"]
        if member.get("kind") == "operation":
            if member_name not in methods:
                _append_operation(cls, name, location, member)
                methods.add(member_name)
        elif member.get("kind") == "attribute":
            if member_name not in attributes:
                _append_attribute(cls, name, location, member)
                attributes.add(member_name)
        elif member.get("kind") == "constant" and location == "interface":
            # Symbolic renderer values are explicitly marked for review. Do
            # not invent a numeric value that would be observably incorrect.
            if member_name not in cls.__dict__:
                if member.get("review") == "manual-review-required":
                    _log("pyv8c: {}.{} 常量待实现", name, member_name)
                    setattr(cls, member_name, pyv8.Undefined)
                else:
                    setattr(cls, member_name, member.get("value"))


def ensure_interface(name: str, baseline=None):
    from .interfaces import IMPLEMENTATIONS, INTERFACES, InterfaceSpec

    baseline = baseline if baseline is not None else load_runtime_baseline()
    if name in _CACHE:
        return _CACHE[name]
    if name not in baseline:
        raise TypeError(f"interface {name!r} is missing from Chromium baseline")
    spec = baseline[name]
    parent_name = spec.get("parent")
    parent_cls = ensure_interface(parent_name, baseline) if parent_name else object
    real_cls = IMPLEMENTATIONS.get(name)
    if real_cls is None:
        cls = type(name, (parent_cls,), {
            "__module__": "pyv8c.interfaces",
            "__init__": _stub_constructor(name),
        })
        INTERFACES[name] = InterfaceSpec(
            name=name, parent=parent_name, constructible=bool(spec.get("constructible")),
            length=int((spec.get("constructor") or {}).get("length") or 0),
            tag=spec.get("tag") or name,
            mixins=tuple(spec.get("mixins") or ()),
            exposed_in=tuple(spec.get("exposed_in") or ()),
        )
        IMPLEMENTATIONS[name] = cls
        cls.__idl_stub__ = True
    else:
        cls = real_cls
        if parent_name and parent_cls not in cls.__mro__[1:]:
            raise TypeError(f"{name} must explicitly inherit {parent_name}")
    cls.__idl_name__ = name
    cls.__idl_parent__ = parent_name
    cls.__idl_baseline__ = spec
    # Existing decorators are the source of truth for implemented classes.
    # The snapshot has incomplete constructor extraction for some bindings
    # (notably URL), so only generated shells inherit these fields wholesale.
    if real_cls is None:
        cls.__v8_length__ = int((spec.get("constructor") or {}).get("length") or 0)
        cls.__v8_constructor__ = FlagConstructor.kNew if spec.get("constructible") else FlagConstructor.kDontAll
        cls.__v8_has_constructor__ = 1
        cls.__v8_to_string_tag__ = spec.get("tag") or name
    _CACHE[name] = cls
    _install_missing_members(cls, name, spec)
    return cls


def ensure_runtime_interfaces(context_type="Window", *, expose_unimplemented=True):
    baseline = load_runtime_baseline()
    # Build all public parent chains first. Unexposed parents remain internal
    # Python classes, while the public child still receives the exact base.
    names = [
        name for name, spec in baseline.items()
        if context_type in (spec.get("exposed_in") or ())
        and (expose_unimplemented or name in __import__("pyv8c.interfaces", fromlist=["IMPLEMENTATIONS"]).IMPLEMENTATIONS)
    ]
    for name in sorted(names):
        ensure_interface(name, baseline)
    return {name: _CACHE[name] for name in names}


def runtime_report(context_type="Window"):
    baseline = load_runtime_baseline()
    from .interfaces import IMPLEMENTATIONS
    report = {"implemented": [], "generated": [], "exposed": [], "skipped": []}
    for name, spec in baseline.items():
        if context_type not in (spec.get("exposed_in") or ()):
            report["skipped"].append(name)
            continue
        report["exposed"].append(name)
        (report["implemented"] if name in IMPLEMENTATIONS and not getattr(IMPLEMENTATIONS[name], "__idl_stub__", False) else report["generated"]).append(name)
    return report
