"""Realm-local binding sessions.

A :class:`BindingSession` owns the class configs of one V8 realm.  Member
wrappers may capture the realm they were built for, so configs are never
shared across realms: each session receives its own config cache and
materializes interface class/config pairs on demand.

Materialization is *commit based*: configs are staged into a private dict and
then pushed to the native Context through ``Context.update_configs``.  The
native side never shares the Python live mapping, so it cannot observe a
partially built overlay while it walks ``class_configs``.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any


class BindingSession:
    """Context-local class configs and callback owners for one V8 realm."""

    def __init__(self, universe, realm=None):
        self.universe = universe
        self.realm = realm
        self._configs: dict[type, dict[str, Any]] = {}
        self._materialized: set[str] = set()
        self._lazy_names: dict[str, tuple[str, ...]] = {}
        self._context = None

    def attach_context(self, context):
        """Record the native Context that received our initial configs.

        Called once the realm Context exists.  From that point on every newly
        materialized config is committed through ``context.update_configs``
        instead of being written into the mapping snapshot the native side
        already holds.
        """
        self._context = context

    def interface_names(self, context_type="Window"):
        return self.universe.interface_names(context_type)

    def class_for(self, name):
        return self.universe.class_for(name)

    @property
    def materialized_interfaces(self):
        return tuple(sorted(self._materialized))

    @property
    def configs(self):
        """Read-only view of the configs materialized for this realm.

        The native Context receives an independent copy at construction time
        and is kept in sync through :meth:`materialize`'s commit step; callers
        must not mutate the mapping behind this view.
        """
        return MappingProxyType(self._configs)

    def _commit(self, staged):
        """Push newly built configs to the attached native Context."""
        self._configs.update(staged)
        context = self._context
        if context is not None:
            update = getattr(context, "update_configs", None)
            if update is not None:
                update(staged)

    def materialize(self, name):
        """Create one interface class/config pair on first access.

        Configs for every ancestor that is not materialized yet are staged in
        a private dict and committed atomically to the native Context; the
        Python live mapping is never handed to the native side.
        """
        chain = list(self.universe.walk_parent_names(name))
        staged = {}
        for interface_name in reversed(chain):
            cls = self.class_for(interface_name)
            if cls not in self._configs:
                self.universe.class_config_for(interface_name, staged, self.realm)
            self._materialized.add(interface_name)
        if staged:
            self._commit(staged)
        return self.class_for(name)

    def class_config_for(self, name):
        self.materialize(name)
        return self._configs[self.class_for(name)]

    def class_configs(self, context_type, *, roots=()):
        names = self.universe.resolve_names(context_type, roots)
        configs = {
            self.universe.class_for(name): self.universe.class_config_for(
                name, self._configs, self.realm,
            )
            for name in names
        }
        # Track interface names, not Python class names: a real implementation
        # class may not be named after the interface it implements.
        self._materialized.update(names)
        return configs

    def lazy_names(self, context_type="Window"):
        """Exposed names that are declared on the realm but not pre-built."""
        if context_type not in self._lazy_names:
            if not self.universe.lazy_interfaces:
                # Everything is materialized up front; nothing to declare lazily.
                self._lazy_names[context_type] = ()
            else:
                eager = set(self.universe.eager_names(context_type))
                self._lazy_names[context_type] = tuple(
                    name for name in self.interface_names(context_type) if name not in eager
                )
        return self._lazy_names[context_type]

    def lazy_factory(self, context_type="Window", on_materialize=None):
        """Build the callable the native Context invokes on first access.

        Returning ``None`` tells the native accessor the name does not belong to
        this realm, which keeps it resolved as ``undefined``. ``on_materialize``
        is notified with each name that was built on demand.
        """
        allowed = set(self.lazy_names(context_type))

        def factory(name):
            name = str(name)
            if name not in allowed:
                return None
            cls = self.materialize(name)
            if on_materialize is not None:
                on_materialize(name)
            return cls

        factory.__name__ = "__pyv8c_lazy_interface"
        return factory
