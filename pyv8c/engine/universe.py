"""Per-Browser interface universe: one baseline view, one compiler, one plan per realm.

The composition root: reading the baseline is :mod:`.baseline`'s business, turning it
into specs is :mod:`.assembly`'s, and this holds what the Browsers share -- the class
each interface name answers with, and the mismatches tolerated once each.
"""

from __future__ import annotations

import threading

from .assembly import InterfaceCompiler
from .baseline import (
    DEFAULT_BASELINE_PATH,
    BaselineRepository,
    BaselineSnapshot,
    InterfaceTable,
    RealmPlan,
    build_plan,
    load_window_surface,
)
from .features import FeatureRuntime, InterfaceSpec
from ..foundation.logging import logger

__all__ = [
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_EAGER_INTERFACES",
    "BaselineRepository",
    "BaselineSnapshot",
    "InterfaceUniverse",
]

DEFAULT_EAGER_INTERFACES = (
    "Window", "EventTarget", "Event", "Node", "Document", "Element",
    "HTMLElement", "URL", "URLSearchParams", "DOMTokenList", "NodeList",
    "HTMLCollection", "NamedNodeMap",
)


class InterfaceUniverse:
    """One isolated baseline view and FeatureRuntime class config set."""

    def __init__(self, baseline: BaselineSnapshot, *, runtime: FeatureRuntime, stub_logging="off",
                 expose_unimplemented=True, baseline_mismatch="warn"):
        self.baseline = baseline
        self.table = InterfaceTable(baseline, surface=load_window_surface())
        self.runtime = runtime
        self.stub_logging = str(stub_logging or "off").lower()
        self.expose_unimplemented = bool(expose_unimplemented)
        if self.stub_logging not in ("on", "verbose", "off"):
            raise ValueError("stub_logging must be 'on', 'verbose' or 'off'")
        self.baseline_mismatch = str(baseline_mismatch or "error").lower()
        if self.baseline_mismatch not in ("error", "warn"):
            raise ValueError("baseline_mismatch must be 'error' or 'warn'")
        #: Reported mismatches, so each difference warns once.
        self._mismatches = set()
        self.eager_interfaces = DEFAULT_EAGER_INTERFACES
        self._validated = False
        # One re-entrant lock guards every read-modify-write cache so concurrent realms never see a torn cache.
        self._lock = threading.RLock()
        self._classes: dict[str, type] = {}
        self._plans: dict[str, RealmPlan] = {}
        self._assembly = InterfaceCompiler(self, stub_logging=self.stub_logging, lock=self._lock)

    def report_mismatch(self, message):
        """Report an implementation/baseline mismatch; true when it is tolerated.

        ``warn`` reports once and lets the caller skip undescribed members; ``error``
        leaves raising to the caller.
        """
        if self.baseline_mismatch != "warn":
            return False
        if message not in self._mismatches:
            self._mismatches.add(message)
            if self.stub_logging != "off":
                logger.warning("pyv8c: {}", message)
        return True

    @property
    def discrepancies(self) -> tuple:
        """Each implementation/baseline difference this universe tolerated, once."""
        return tuple(sorted(self._mismatches))

    def spec(self, name):
        """The raw baseline spec of one interface."""
        try:
            return self.table.raw(name)
        except KeyError as exc:
            if self.report_mismatch(f"interface {name!r} is missing from the interface baseline"):
                return {}
            raise TypeError(f"interface {name!r} is missing from Chromium interface baseline") from exc

    def plan(self, context_type="Window"):
        """What a realm of *context_type* installs, computed once."""
        with self._lock:
            cached = self._plans.get(context_type)
            if cached is None:
                cached = build_plan(
                    self.table, context_type,
                    implements=self.runtime.catalog.interfaces,
                    expose_unimplemented=self.expose_unimplemented,
                    eager=self.eager_interfaces,
                )
                self._plans[context_type] = cached
            return cached

    def class_for(self, name):
        """Return the one canonical class object per interface name.

        The lock makes creation idempotent across realms: two pages materializing the
        same lazy interface must not build distinct classes, because the native
        template cache keys on the class and cross-realm ``instanceof`` would break.
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
                    "__init__": self._assembly.stubs.constructor(name),
                    "__idl_spec__": InterfaceSpec(name, parent_name),
                    "__v8_idl_stub__": True,
                })
            else:
                cls = implementation
            self._classes[name] = cls
            return cls

    def class_config_for(self, name, realm=None):
        """The spec one interface is built from in one realm."""
        return self._assembly.bind(name, realm)

    def describes(self, name):
        """Whether this universe can commit an overlay for ``name``."""
        if name not in self.baseline.interfaces:
            return False
        return getattr(self.class_for(name), "__name__", None) == name

    def validate(self):
        """Compile every interface once, to surface mismatches before a page runs."""
        with self._lock:
            if not self._validated:
                for name in self.baseline.interfaces:
                    self._assembly.compile(name)
                self._validated = True
