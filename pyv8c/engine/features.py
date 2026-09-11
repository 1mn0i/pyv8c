"""Declarative Web API features and their on-demand runtime.

Feature declarations are imported at bootstrap time; implementation modules are
not.  A feature owns every interface and Window value it supplies, so module
loading, interface resolution and per-Window value identity share one model.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from threading import RLock, local
from types import MappingProxyType


@dataclass(frozen=True)
class InterfaceSpec:
    name: str
    parent: str | None
    exposed_in: tuple[str, ...] = ("Window", "Worker")


def interface(name, parent, *, exposed_in=("Window", "Worker")):
    declaration = InterfaceSpec(name, parent, tuple(exposed_in))

    def decorate(cls):
        cls.__idl_spec__ = declaration
        return cls

    return decorate


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    module: str
    interfaces: tuple[str, ...]
    window_slots: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    eager: bool = False
    atomic: bool = True


class FeatureImplementation:
    """The single explicit export required from an implementation module."""

    def __init__(self, interfaces, window_values=()):
        self.interfaces = MappingProxyType(dict(interfaces))
        self.window_values = MappingProxyType(dict(window_values))

    def create_window_value(self, slot, window):
        try:
            return self.window_values[slot](window)
        except KeyError as exc:
            raise RuntimeError(f"feature has no Window value {slot!r}") from exc


class FeatureCatalog:
    """Immutable indexes built from small, import-free feature declarations."""

    def __init__(self, specs):
        by_name, by_interface, by_slot = {}, {}, {}
        for spec in specs:
            if not isinstance(spec, FeatureSpec):
                raise TypeError("features must be FeatureSpec instances")
            if not spec.name or spec.name in by_name:
                raise ValueError(f"duplicate feature: {spec.name!r}")
            names = tuple(dict.fromkeys(spec.interfaces))
            slots = tuple(dict.fromkeys(spec.window_slots))
            if not names:
                raise ValueError(f"feature {spec.name!r} has no interfaces")
            if names != spec.interfaces or slots != spec.window_slots:
                raise ValueError(f"feature {spec.name!r} contains duplicate names")
            for name in names:
                if name in by_interface:
                    raise ValueError(f"interface {name!r} belongs to multiple features")
                by_interface[name] = spec
            for slot in slots:
                if slot in by_slot:
                    raise ValueError(f"Window slot {slot!r} belongs to multiple features")
                by_slot[slot] = spec
            by_name[spec.name] = spec
        for spec in by_name.values():
            missing = set(spec.requires) - set(by_name)
            if missing:
                raise ValueError(f"feature {spec.name!r} requires unknown features: {sorted(missing)!r}")
        self._by_name = MappingProxyType(by_name)
        self._by_interface = MappingProxyType(by_interface)
        self._by_slot = MappingProxyType(by_slot)

    @property
    def interfaces(self):
        return frozenset(self._by_interface)

    @property
    def specs(self):
        return self._by_name

    def feature(self, name):
        return self._by_name[name]

    def for_interface(self, name):
        return self._by_interface.get(name)

    def for_window_slot(self, slot):
        return self._by_slot.get(slot)

    def eager(self):
        return tuple(spec for spec in self._by_name.values() if spec.eager)


class WindowFeatureValues:
    """Per-Window identity cache owned by one :class:`FeatureRuntime`."""

    __slots__ = ("_runtime", "_window", "_values")

    def __init__(self, runtime, window):
        self._runtime, self._window, self._values = runtime, window, {}

    def value(self, slot):
        try:
            return self._values[slot]
        except KeyError:
            value = self._runtime.create_window_value(slot, self._window)
            self._values[slot] = value
            return value


class FeatureRuntime:
    """Loads feature modules once and exposes their classes to one Universe."""

    def __init__(self, catalog, *, importer=import_module):
        self.catalog = catalog
        self._importer = importer
        self._loaded = {}
        self._interfaces = {}
        self._lock = RLock()
        self._stack = local()

    @property
    def loaded_features(self):
        with self._lock:
            return frozenset(self._loaded)

    @property
    def implementations(self):
        with self._lock:
            return MappingProxyType(dict(self._interfaces))

    def preload_eager(self):
        for spec in self.catalog.eager():
            self.load(spec.name)

    def load(self, name):
        spec = self.catalog.feature(name)
        with self._lock:
            loaded = self._loaded.get(name)
            if loaded is not None:
                return loaded
            stack = getattr(self._stack, "names", ())
            if name in stack:
                cycle = " -> ".join((*stack, name))
                raise RuntimeError(f"cyclic feature dependency: {cycle}")
            self._stack.names = (*stack, name)
            try:
                for dependency in spec.requires:
                    self.load(dependency)
                module = self._importer(spec.module)
                implementation = getattr(module, "FEATURE", None)
                if not isinstance(implementation, FeatureImplementation):
                    raise TypeError(f"feature module {spec.module!r} must export FEATURE")
                actual = implementation.interfaces
                if set(actual) != set(spec.interfaces):
                    raise RuntimeError(
                        f"feature {name!r} interfaces differ from its declaration: "
                        f"expected={spec.interfaces!r}, actual={tuple(actual)!r}"
                    )
                for interface_name, cls in actual.items():
                    declaration = getattr(cls, "__idl_spec__", None)
                    if getattr(declaration, "name", None) != interface_name:
                        raise TypeError(f"feature {name!r} exports an invalid interface {interface_name!r}")
                    parent_name = getattr(declaration, "parent", None)
                    if parent_name is not None:
                        parent_names = {
                            getattr(base.__dict__.get("__idl_spec__"), "name", None)
                            for base in cls.__mro__[1:]
                        }
                        if parent_name not in parent_names:
                            raise TypeError(
                                f"feature {name!r} interface {interface_name!r} "
                                f"does not inherit {parent_name!r}"
                            )
                declared_slots = set(spec.window_slots)
                actual_slots = set(implementation.window_values)
                if actual_slots != declared_slots:
                    missing_slots = sorted(declared_slots - actual_slots)
                    extra_slots = sorted(actual_slots - declared_slots)
                    raise RuntimeError(
                        f"feature {name!r} Window slots differ from its declaration: "
                        f"missing={missing_slots!r}, extra={extra_slots!r}"
                    )
                self._loaded[name] = implementation
                self._interfaces.update(actual)
                return implementation
            finally:
                self._stack.names = stack

    def interface(self, name):
        spec = self.catalog.for_interface(name)
        if spec is None:
            return None
        return self.load(spec.name).interfaces[name]

    def interface_group(self, name):
        spec = self.catalog.for_interface(name)
        return spec.interfaces if spec is not None and spec.atomic else (name,)

    def values_for(self, window):
        return WindowFeatureValues(self, window)

    def create_window_value(self, slot, window):
        spec = self.catalog.for_window_slot(slot)
        if spec is None:
            raise AttributeError(f"unknown Window feature slot: {slot!r}")
        return self.load(spec.name).create_window_value(slot, window)

    def validate(self):
        for name in self.catalog.specs:
            self.load(name)
