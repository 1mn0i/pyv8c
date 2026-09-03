"""Realm-local binding sessions.

A :class:`BindingSession` owns the class configs of one V8 realm.  Member
wrappers may capture the realm they were built for, so configs are never
shared across realms: each session receives its own config cache and
materializes interface class/config pairs on demand.
"""

from __future__ import annotations

from typing import Any


class BindingSession:
    """Context-local class configs and callback owners for one V8 realm."""

    def __init__(self, universe, realm=None):
        self.universe = universe
        self.realm = realm
        self._configs: dict[type, dict[str, Any]] = {}
        self._materialized: set[str] = set()
        self._lazy_names: dict[str, tuple[str, ...]] = {}

    def interface_names(self, context_type="Window"):
        return self.universe.interface_names(context_type)

    def class_for(self, name):
        return self.universe.class_for(name)

    @property
    def materialized_interfaces(self):
        return tuple(sorted(self._materialized))

    @property
    def configs(self):
        """Live mapping consumed by native Context implementations."""
        return self._configs

    def materialize(self, name):
        """Create one interface class/config pair on first access."""
        chain = list(self.universe.walk_parent_names(name))
        for interface_name in reversed(chain):
            cls = self.class_for(interface_name)
            config = self.universe.class_config_for(interface_name, self._configs, self.realm)
            self._materialized.add(interface_name)
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
