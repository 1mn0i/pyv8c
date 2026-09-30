"""Realm-local binding state with shared immutable compiled descriptions.

Only realm-dependent callbacks are rebound; configs and materialized names become
visible after the native batch commit succeeds.
"""

from __future__ import annotations

from types import MappingProxyType

from pyv8 import IdlSpec


class BindingSession:
    """Context-local registration state for one V8 realm."""

    def __init__(self, universe, realm=None):
        self.universe = universe
        self.realm = realm
        self._configs: dict[type, IdlSpec] = {}
        self._materialized: set[str] = set()
        self._lazy_names: dict[str, tuple[str, ...]] = {}
        self._context = None

    def attach_context(self, context):
        """Record the native Context that received our initial configs.

        From then on every newly materialized config is committed through
        ``context.update_configs`` rather than the mapping snapshot the native side holds.
        """
        self._context = context

    def detach_context(self):
        self._context = None
        self._configs.clear()
        self._materialized.clear()
        self._lazy_names.clear()
        self.realm = None

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

        The native Context holds an independent copy kept in sync by :meth:`materialize`'s
        commit step; callers must not mutate the mapping behind this view.
        """
        return MappingProxyType(self._configs)

    def _materialize(self, names):
        staged = {}
        materialized = set()
        for name in names:
            for group_name in self.universe.runtime.interface_group(name):
                for interface_name in reversed(tuple(self.universe.walk_parent_names(group_name))):
                    cls = self.class_for(interface_name)
                    if cls not in self._configs and cls not in staged:
                        staged[cls] = self.universe.class_config_for(interface_name, self.realm)
                    materialized.add(interface_name)
        if staged and self._context is not None:
            self._context.update_configs(staged)
        self._configs.update(staged)
        self._materialized.update(materialized)

    def materialize(self, name):
        self._materialize((name,))
        return self.class_for(name)

    def class_config_for(self, name):
        self.materialize(name)
        return self._configs[self.class_for(name)]

    def class_configs(self, context_type, *, roots=()):
        self._materialize(self.universe.resolve_names(context_type, roots))
        return self.configs

    def lazy_names(self, context_type="Window"):
        """Exposed names that are declared on the realm but not pre-built."""
        if context_type not in self._lazy_names:
            eager = set(self.universe.eager_names(context_type))
            self._lazy_names[context_type] = tuple(
                name for name in self.interface_names(context_type) if name not in eager
            )
        return self._lazy_names[context_type]

    def lazy_factory(self, context_type="Window", on_materialize=None):
        """Build the callable the native Context invokes on first access.

        Returning ``None`` marks the name as not belonging to this realm, keeping it
        ``undefined``; ``on_materialize`` is notified with each on-demand name.

        The realm declares every exposed name, so the binding asks about all of them;
        the eager core is already built by then and answers from the cache.  The realm
        also declares the interfaces it hands over without exposing them, so those are
        answered too: their overlay has to be committed before the object's template is
        built.  A name the universe cannot describe answers ``None``, which leaves the
        class declaration in place.
        """
        allowed = set(self.interface_names(context_type))

        def factory(name):
            name = str(name)
            if name not in allowed and not self.universe.describes(name):
                return None
            cls = self.materialize(name)
            if on_materialize is not None:
                on_materialize(name)
            return cls

        factory.__name__ = "__pyv8c_lazy_interface"
        return factory
