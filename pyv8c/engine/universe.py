"""Per-Browser interface universe: materialization, stubs and binding sessions.

Baseline file I/O and the two-level disk cache live in :mod:`.baseline`;
this module keeps the per-Browser :class:`InterfaceUniverse` composition
root.  The collaborators it assembles are: :mod:`.catalog` (hierarchy and
exposure indexes), :mod:`.stubs` (generated API shells for unimplemented
members), :mod:`.wrappers` (argument-validating callback wrappers),
:mod:`.assembly` (native ClassConfig assembly) and :mod:`.sessions` (the
realm-local :class:`BindingSession`).
"""

from __future__ import annotations

import threading

from .baseline import (
    DEFAULT_BASELINE_PATH,
    BaselineRepository,
    BaselineSnapshot,
)
from .webidl import BindingPlan, compile_binding
from .catalog import InterfaceCatalog
from .stubs import StubFactory
from .wrappers import BindingWrapperFactory
from .assembly import ClassConfigBuilder, own_configs
from .sessions import BindingSession

__all__ = [
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_EAGER_INTERFACES",
    "BaselineRepository",
    "BaselineSnapshot",
    "InterfaceUniverse",
    "BindingSession",
]

DEFAULT_EAGER_INTERFACES = (
    "Window", "EventTarget", "Event", "Node", "Document", "Element",
    "HTMLElement", "URL", "URLSearchParams", "DOMTokenList", "NodeList",
    "HTMLCollection", "NamedNodeMap", "PluginArray", "Plugin", "MimeTypeArray",
    "MimeType",
)


class InterfaceUniverse:
    """One isolated interface registry, binding plan, and class config set."""

    def __init__(self, baseline: BaselineSnapshot, *, registry, stub_logging="info",
                 expose_unimplemented=True, eager_interfaces=DEFAULT_EAGER_INTERFACES,
                 strict_validation=False, lazy_interfaces=True):
        self.baseline = baseline
        self.registry = registry
        self.stub_logging = str(stub_logging or "info").lower()
        self.expose_unimplemented = bool(expose_unimplemented)
        self.eager_interfaces = tuple(dict.fromkeys(eager_interfaces or ()))
        self.strict_validation = bool(strict_validation)
        self.lazy_interfaces = bool(lazy_interfaces)
        # Realm-independent caches. Configs are owned by each BindingSession.
        # One re-entrant lock guards every read-modify-write cache sequence so
        # concurrent materialisation across realms can never observe a torn
        # cache (two distinct class objects for one interface name, a plan
        # inserted twice, or an exposure table computed twice).
        self._lock = threading.RLock()
        self._classes: dict[str, type] = {}
        self._plans: dict[tuple[str, str, str], BindingPlan] = {}
        self._catalog = InterfaceCatalog(baseline)
        self._exposure_names = {}
        # Collaborator factories: stubs generate unimplemented-member shells,
        # wrappers adapt hand-written callbacks with argument validation, and
        # the assembler merges both into the native ClassConfig wire shape.
        self._stubs = StubFactory(self.stub_logging)
        self._wrappers = BindingWrapperFactory(self)
        self._assembly = ClassConfigBuilder(self, self._stubs, self._wrappers)

    def spec(self, name):
        """The raw baseline spec of one interface (collaborator query surface)."""
        try:
            return self.baseline.interfaces[name]
        except KeyError as exc:
            raise TypeError(f"interface {name!r} is missing from Chromium interface baseline") from exc

    def own_members(self, interface_name):
        """Yield ``(location, member)`` declared directly on one interface."""
        spec = self.spec(interface_name)
        for location in ("instance", "prototype", "interface"):
            for member in (spec.get("members") or {}).get(location, ()) or ():
                yield location, member

    def interface_names(self, context_type="Window"):
        """Return the stable global name table without materializing classes."""
        with self._lock:
            cached = self._exposure_names.get(context_type)
        if cached is not None:
            return cached
        names = tuple(sorted(self.exposed_names(context_type)))
        with self._lock:
            cached = self._exposure_names.get(context_type)
            if cached is not None:
                return cached
            self._exposure_names[context_type] = names
            return names

    def eager_names(self, context_type="Window"):
        exposed = set(self.interface_names(context_type))
        names = set(name for name in self.eager_interfaces if name in exposed or name == "Window")
        return tuple(name for name in self.interface_names(context_type) if name in names)

    def _member(self, interface_name, public_name, kind):
        seen, current = set(), interface_name
        while current and current not in seen:
            seen.add(current)
            candidates = [
                (location, member) for location, member in self.own_members(current)
                if member.get("name") == public_name and member.get("kind") == kind
            ]
            if candidates:
                candidates.sort(key=lambda item: (
                    item[1].get("world") not in (None, "AllWorlds", "MainWorld"),
                    (item[1].get("signature") or {}).get("status") == "unresolved",
                ))
                return current, candidates[0][0], candidates[0][1]
            current = self.spec(current).get("parent")
        # Blink sometimes installs WorkerGlobalScope members in a concrete
        # descendant binding table. Keep this lookup explicit and deterministic.
        for candidate_name in self._catalog.descendants(interface_name):
            for location, member in self.own_members(candidate_name):
                if member.get("name") == public_name and member.get("kind") == kind:
                    return candidate_name, location, member
        return None

    def binding_plan(self, interface_name, public_name, kind="operation"):
        key = (interface_name, public_name, kind)
        with self._lock:
            plan = self._plans.get(key)
            if plan is not None:
                return plan
        if kind == "constructor":
            signature = (self.spec(interface_name).get("constructor") or {}).get("signature")
        else:
            found = self._member(interface_name, public_name, "attribute" if kind == "attribute" else "operation")
            if found is None:
                raise TypeError(f"{interface_name}.{public_name} is not present in Chromium interface baseline")
            _owner, _location_name, member = found
            signature = member.get("setter_signature") if kind == "attribute" else member.get("signature")
        plan = compile_binding(interface_name, public_name, kind, signature)
        with self._lock:
            existing = self._plans.get(key)
            if existing is not None:
                return existing
            self._plans[key] = plan
            return plan

    def class_for(self, name):
        """Return the one canonical class object per interface name.

        The lock makes class creation idempotent under concurrent access from
        several realms: without it, two pages materialising the same lazy
        interface could build two distinct classes and break cross-realm
        ``instanceof`` because the native template cache keys on the class.
        """
        with self._lock:
            cls = self._classes.get(name)
            if cls is not None:
                return cls
            spec = self.spec(name)
            parent_name = spec.get("parent")
            parent = self.class_for(parent_name) if parent_name else object
            implementation = self.registry.implementations.get(name)
            if implementation is None:
                cls = type(name, (parent,), {
                    "__module__": "pyv8c.engine.generated",
                    "__init__": self._stubs.constructor(name),
                    "__idl_name__": name,
                    "__idl_parent__": parent_name,
                    "__v8_idl_stub__": True,
                })
            else:
                cls = implementation
            self._classes[name] = cls
            return cls

    def class_config_for(self, name, cache=None, realm=None):
        """Assemble the config of one interface.

        Called without a cache for inspection outside of any realm;
        :class:`BindingSession` passes its own cache plus the realm so member
        wrappers can capture it.  Configs are owned by sessions, never shared
        across realms.
        """
        return self._assembly.build(name, {} if cache is None else cache, realm)

    def walk_parent_names(self, name):
        """The interface plus its ancestor chain, innermost first."""
        yield from self._catalog.walk_parent_names(name)

    def exposed_names(self, context_type):
        return list(self._catalog.exposed_names(
            context_type, self.expose_unimplemented, self.registry.implementations,
        ))

    def resolve_names(self, context_type, roots=()):
        """Interface names (including ancestors) a context_type must install."""
        names = set(
            self.eager_names(context_type)
            if self.lazy_interfaces else self.exposed_names(context_type)
        )
        names.update(root for root in roots if isinstance(root, str))
        for name in tuple(names):
            names.update(self.walk_parent_names(name))
        return names

    def binding_session(self, realm=None):
        return BindingSession(self, realm)

    def validate(self):
        self.registry.validate(self.baseline.interfaces)
        for name, interface_spec in self.registry.specs.items():
            cls = self.registry.implementations[name]
            direct_members = [member for _location_name, member in self.own_members(name)]
            operation_names = {member.get("name") for member in direct_members if member.get("kind") == "operation"}
            attribute_names = {member.get("name") for member in direct_members if member.get("kind") == "attribute"}
            for config in own_configs(cls, "__v8_method__", operation_names):
                plan = self.binding_plan(name, config[0])
                if not plan.resolved:
                    raise TypeError(
                        f"{name}.{config[0]} has unresolved Web IDL signature"
                        + (f": {plan.source_evidence}" if plan.source_evidence else "")
                    )
            for config in own_configs(cls, "__v8_attribute__", attribute_names):
                if config[2]:
                    plan = self.binding_plan(name, config[0], "attribute")
                    if not plan.resolved:
                        raise TypeError(f"{name}.{config[0]} setter has unresolved Web IDL signature")
            if interface_spec.constructible:
                plan = self.binding_plan(name, "constructor", "constructor")
                if not plan.resolved:
                    raise TypeError(f"{name} constructor has unresolved Web IDL signature")
