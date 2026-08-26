from dataclasses import dataclass, field
from typing import Dict, Optional, Type

from pyv8.flag import (
    FlagConstructor,
    FlagLocation,
    FlagReceiverCheck,
    FlagCrossOriginCheck,
)


@dataclass(frozen=True)
class InterfaceSpec:
    name: str
    parent: Optional[str]
    constructible: bool = False
    length: int = 0
    tag: str = ""
    mixins: tuple[str, ...] = field(default_factory=tuple)
    exposed_in: tuple[str, ...] = ("Window", "Worker")


INTERFACES: Dict[str, InterfaceSpec] = {}
IMPLEMENTATIONS: Dict[str, Type] = {}


def load_baseline(path=None):
    """Load a baseline for tooling and backwards-compatible callers."""
    from .interface_runtime import BaselineRepository
    return dict(BaselineRepository.load(path).interfaces)


def interface(name: str, parent: Optional[str], *, constructible=False, length=0,
              tag: Optional[str] = None, mixins=(), exposed_in=("Window", "Worker")):
    """Declare the Web IDL interface independently from Python inheritance."""
    spec = InterfaceSpec(name, parent, constructible, length, tag or name,
                         tuple(mixins), tuple(exposed_in))

    def decorate(cls):
        if name in INTERFACES:
            raise TypeError(f"duplicate interface: {name}")
        INTERFACES[name] = spec
        IMPLEMENTATIONS[name] = cls
        cls.__idl_name__ = name
        cls.__idl_parent__ = parent
        cls.__idl_spec__ = spec
        cls.__v8_length__ = length
        cls.__v8_constructor__ = FlagConstructor.kNew if constructible else FlagConstructor.kDontAll
        cls.__v8_has_constructor__ = 1
        cls.__v8_to_string_tag__ = spec.tag
        return cls

    return decorate


def validate_registry(baseline=None):
    baseline = baseline if baseline is not None else load_baseline()
    def baseline_members(interface_name):
        root_interface = interface_name
        names = set()
        seen = set()
        while interface_name and interface_name not in seen:
            seen.add(interface_name)
            entry = baseline.get(interface_name, {}) if baseline else {}
            names.update(member.get("name") for values in entry.get("members", {}).values() for member in values)
            interface_name = entry.get("parent")
        # Blink may install shared WorkerGlobalScope operations in the
        # concrete Dedicated/SharedWorker binding file rather than the base
        # interface's file. Include those descendant tables for validation.
        if baseline:
            for child, entry in baseline.items():
                cursor, child_seen = child, set()
                while cursor and cursor not in child_seen:
                    child_seen.add(cursor)
                    if cursor == root_interface:
                        break
                    cursor = baseline.get(cursor, {}).get("parent")
                if child_seen and root_interface in child_seen:
                    names.update(member.get("name") for values in entry.get("members", {}).values() for member in values)
        return names
    for name, spec in INTERFACES.items():
        if spec.parent and spec.parent not in INTERFACES:
            raise TypeError(f"{name} refers to unknown parent {spec.parent}")
        cls = IMPLEMENTATIONS[name]
        if spec.parent:
            parent_cls = IMPLEMENTATIONS[spec.parent]
            if parent_cls not in cls.__mro__[1:]:
                raise TypeError(f"{name} must explicitly inherit {spec.parent}")
        if baseline:
            if name not in baseline:
                raise TypeError(f"{name} is not present in Chromium interface baseline")
            expected = baseline[name].get("parent")
            # A renderer snapshot may expose a more specific parent than the
            # reduced runtime model.  For implemented interfaces we still
            # reject an explicit conflicting registration, while allowing a
            # missing snapshot parent to remain unresolved for review.
            if expected is not None and expected != spec.parent:
                raise TypeError(f"{name} parent {spec.parent!r} does not match baseline {expected!r}")
            expected_exposure = baseline[name].get("exposed_in")
            if expected_exposure is not None and not (set(expected_exposure) & set(spec.exposed_in)):
                raise TypeError(f"{name} exposure {spec.exposed_in!r} does not overlap baseline {expected_exposure!r}")
            expected_tag = baseline[name].get("tag")
            if expected_tag and expected_tag != spec.tag:
                raise TypeError(f"{name} tag {spec.tag!r} does not match baseline {expected_tag!r}")
        cls_methods = getattr(cls, "__v8_method__", ()) or ()
        available_members = baseline_members(name)
        for member in cls_methods:
            public_name, _callback, length = member[0], member[1], member[2]
            if baseline and public_name not in available_members:
                raise TypeError(f"{name}.{public_name} is not present in Chromium interface baseline")


def expose_interfaces(context, context_type="Window", *, generate_stubs=True,
                      expose_unimplemented=True, stub_logging="info", universe=None):
    """Expose public interfaces, filling baseline-only APIs with shells.

    The baseline contains renderer-only and conditionally exposed bindings;
    only entries explicitly exposed in the current execution context reach JS.
    """
    if universe is None:
        from .interface_runtime import BaselineRepository, InterfaceUniverse
        universe = InterfaceUniverse(
            BaselineRepository.load(), stub_logging=stub_logging,
            expose_unimplemented=expose_unimplemented,
        )
    if generate_stubs:
        universe.validate()
    else:
        validate_registry(universe.baseline.interfaces)
    # Sort by inheritance depth so a generated/real parent template is known
    # before its child is installed by the native pyv8 bridge.
    def depth(name):
        value, seen = 0, set()
        parent = (
            universe.baseline.interfaces.get(name, {}).get("parent")
            if generate_stubs else getattr(INTERFACES.get(name), "parent", None)
        )
        while parent and parent not in seen:
            seen.add(parent)
            value += 1
            parent = (
                universe.baseline.interfaces.get(parent, {}).get("parent")
                if generate_stubs else getattr(INTERFACES.get(parent), "parent", None)
            )
        return value
    names = universe.exposed_names(context_type) if generate_stubs else [name for name, spec in INTERFACES.items() if context_type in spec.exposed_in]
    for name in sorted(names, key=depth):
        context.expose(universe.class_for(name) if generate_stubs else IMPLEMENTATIONS[name], name=name)


def js_attributes(*items):
    return tuple((name, getter, setter, 0, 1, flags, 0, 0, 0, side_effect)
                 for name, getter, setter, flags, side_effect in items)


def js_methods(*items):
    return tuple((name, callback, length, 0, 1, 0, 0, 0, side_effect)
                 for name, callback, length, side_effect in items)


def js_interface_methods(*items):
    """Declare Web IDL operations installed on the interface constructor."""
    return tuple((name, callback, length, 0, FlagLocation.kInterface, 0,
                  FlagReceiverCheck.kDoNotCheck,
                  FlagCrossOriginCheck.kDoNotCheck, side_effect)
                 for name, callback, length, side_effect in items)
