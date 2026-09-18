"""Per-Browser interface universe: materialization, stubs and binding sessions.

Baseline file I/O and the two-level disk cache live in :mod:`.baseline`;
this module keeps the per-Browser :class:`InterfaceUniverse` composition
root.  The collaborators it assembles are: :mod:`.catalog` (hierarchy and
exposure indexes), :mod:`.stubs` (generated API shells for unimplemented
members), :mod:`.wrappers` (realm injection adapters),
:mod:`.assembly` (immutable IDL compilation) and :mod:`.sessions` (the
realm-local :class:`BindingSession`).
"""

from __future__ import annotations

import threading

from .assembly import InterfaceCompiler
from .baseline import (
    DEFAULT_BASELINE_PATH,
    BaselineRepository,
    BaselineSnapshot,
)
from .catalog import InterfaceCatalog
from .features import FeatureRuntime, InterfaceSpec
from .sessions import BindingSession
from .stubs import StubFactory
from .wrappers import BindingWrapperFactory

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
    "HTMLCollection", "NamedNodeMap",
)

class InterfaceUniverse:
    """One isolated baseline view and FeatureRuntime class config set."""

    def __init__(self, baseline: BaselineSnapshot, *, runtime: FeatureRuntime, stub_logging="info",
                 expose_unimplemented=True):
        self.baseline = baseline
        self.runtime = runtime
        self.stub_logging = str(stub_logging or "info").lower()
        self.expose_unimplemented = bool(expose_unimplemented)
        self.eager_interfaces = DEFAULT_EAGER_INTERFACES
        self._validated = False
        self.deferred_globals = True
        # Realm-independent caches. Configs are owned by each BindingSession.
        # One re-entrant lock guards every read-modify-write cache sequence so
        # concurrent materialisation across realms can never observe a torn
        # cache (two distinct class objects for one interface name, a plan
        # inserted twice, or an exposure table computed twice).
        self._lock = threading.RLock()
        self._classes: dict[str, type] = {}
        self._catalog = InterfaceCatalog(baseline)
        self._exposure_names = {}
        # Collaborator factories: stubs generate unimplemented-member shells,
        # adapters inject realm dependencies into callbacks, and
        # the compiler combines both into immutable native descriptions.
        self._stubs = StubFactory(self.stub_logging)
        self._wrappers = BindingWrapperFactory(self)
        self._assembly = InterfaceCompiler(self, self._stubs, self._wrappers, self._lock)

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
            implementation = self.runtime.interface(name)
            if implementation is None:
                cls = type(name, (parent,), {
                    "__module__": "pyv8c.engine.generated",
                    "__init__": self._stubs.constructor(name),
                    "__idl_spec__": InterfaceSpec(name, parent_name),
                    "__v8_idl_stub__": True,
                })
            else:
                cls = implementation
            self._classes[name] = cls
            return cls

    def plan_for(self, name, realm=None):
        """The install plan for one interface, as a realm hands it to the batch.

        A plan carries callables rather than slot numbers, so it is correct in
        every realm that installs it -- which is why it is compiled once and
        cached, and why the one thing that does depend on the realm produces a
        copy instead.
        """
        return self._assembly.plan(name, realm)

    def plan_declared(self, cls, name, declaration=None, realm=None):
        """The plan for a class that carries its own declaration.

        For the interfaces the renderer's snapshot does not describe but pyv8c
        defines anyway: the class's declaration is the whole interface.
        """
        return self._assembly.plan_declared(cls, name, declaration, realm)

    def walk_parent_names(self, name):
        """The interface plus its ancestor chain, innermost first."""
        yield from self._catalog.walk_parent_names(name)

    def exposed_names(self, context_type):
        return list(self._catalog.exposed_names(
            context_type, self.expose_unimplemented, self.runtime.catalog.interfaces,
        ))

    def resolve_names(self, context_type, roots=()):
        """Interface names (including ancestors) a context_type must install."""
        names = set(self.eager_names(context_type))
        names.update(root for root in roots if isinstance(root, str))
        for name in tuple(names):
            names.update(self.walk_parent_names(name))
        return names

    def binding_session(self, realm=None):
        return BindingSession(self, realm)

    def validate(self):
        with self._lock:
            if not self._validated:
                for name in self.baseline.interfaces:
                    self._assembly.compile(name)
                self._validated = True
