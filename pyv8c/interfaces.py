from dataclasses import dataclass, field
from typing import Dict, Optional, Type
import json
from pathlib import Path

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagConstructor, FlagExposed


@dataclass(frozen=True)
class InterfaceSpec:
    name: str
    parent: Optional[str]
    constructible: bool = False
    length: int = 0
    tag: str = ""
    mixins: tuple[str, ...] = field(default_factory=tuple)


INTERFACES: Dict[str, InterfaceSpec] = {}
IMPLEMENTATIONS: Dict[str, Type] = {}


def load_baseline(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / "interface-baseline.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def interface(name: str, parent: Optional[str], *, constructible=False, length=0,
              tag: Optional[str] = None, mixins=()):
    """Declare the Web IDL interface independently from Python inheritance."""
    spec = InterfaceSpec(name, parent, constructible, length, tag or name, tuple(mixins))

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
            if expected != spec.parent:
                raise TypeError(f"{name} parent {spec.parent!r} does not match baseline {expected!r}")


def expose_interfaces(context):
    validate_registry()
    for name, cls in IMPLEMENTATIONS.items():
        context.expose(cls, name=name)


def js_attributes(*items):
    return tuple((name, getter, setter, 0, 1, flags, 0, 0, 0, side_effect)
                 for name, getter, setter, flags, side_effect in items)


def js_methods(*items):
    return tuple((name, callback, length, 0, 1, 0, 0, 0, side_effect)
                 for name, callback, length, side_effect in items)
