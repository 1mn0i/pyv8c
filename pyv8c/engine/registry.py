from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class InterfaceSpec:
    name: str
    parent: str | None
    exposed_in: tuple[str, ...] = ("Window", "Worker")


class InterfaceRegistry:
    def __init__(self):
        self._implementations = {}
        self._specs = {}
        self._frozen = False

    @property
    def implementations(self):
        return MappingProxyType(self._implementations)

    @property
    def specs(self):
        return MappingProxyType(self._specs)

    @property
    def frozen(self):
        return self._frozen

    def register(self, cls):
        if self._frozen:
            raise RuntimeError("interface registry is frozen")
        spec = cls.__dict__.get("__idl_spec__")
        if not isinstance(spec, InterfaceSpec):
            raise TypeError(f"{cls!r} is not an interface declaration")
        existing = self._implementations.get(spec.name)
        if existing is not None and existing is not cls:
            raise TypeError(f"duplicate interface: {spec.name}")
        self._specs[spec.name] = spec
        self._implementations[spec.name] = cls

    def register_namespace(self, namespace):
        for cls in namespace.values():
            if isinstance(cls, type) and isinstance(cls.__dict__.get("__idl_spec__"), InterfaceSpec):
                self.register(cls)

    def freeze(self):
        self._frozen = True
        return self


def interface(name, parent, *, exposed_in=("Window", "Worker")):
    declaration = InterfaceSpec(name, parent, tuple(exposed_in))

    def decorate(cls):
        cls.__idl_spec__ = declaration
        return cls

    return decorate


def expose_interfaces(context, context_type="Window", *, universe, generate_stubs=True, class_for=None):
    """Expose public interfaces, filling baseline-only APIs with shells.

    The baseline contains renderer-only and conditionally exposed bindings;
    only entries explicitly exposed in the current execution context reach JS.
    """
    registry = universe.registry

    # Sort by inheritance depth so a generated/real parent template is known
    # before its child is installed by the native pyv8 bridge.
    def depth(name):
        value, seen = 0, set()
        parent = (
            universe.baseline.interfaces.get(name, {}).get("parent")
            if generate_stubs
            else getattr(registry.specs.get(name), "parent", None)
        )
        while parent and parent not in seen:
            seen.add(parent)
            value += 1
            parent = (
                universe.baseline.interfaces.get(parent, {}).get("parent")
                if generate_stubs
                else getattr(registry.specs.get(parent), "parent", None)
            )
        return value

    if generate_stubs and universe.lazy_interfaces:
        names = universe.eager_names(context_type)
    elif generate_stubs:
        names = universe.exposed_names(context_type)
    else:
        names = [name for name, spec in registry.specs.items() if context_type in spec.exposed_in]
    for name in sorted(names, key=depth):
        resolver = class_for or universe.class_for
        context.expose(resolver(name) if generate_stubs else registry.implementations[name], name=name)
