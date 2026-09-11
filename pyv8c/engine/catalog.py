"""Baseline catalog helpers used by :mod:`bindings.universe`.

The catalog owns indexes derived from an immutable baseline.  Binding sessions
remain responsible for realm-local configs and callback wrappers.
"""

from __future__ import annotations


class InterfaceCatalog:
    """Precomputed hierarchy and exposure indexes for one baseline."""

    def __init__(self, baseline):
        self.baseline = baseline
        self._children = None
        self._positions = None
        self._exposed = {}

    def positions(self):
        if self._positions is None:
            self._positions = {name: i for i, name in enumerate(self.baseline.interfaces)}
        return self._positions

    def children(self):
        if self._children is None:
            index = {}
            for name, spec in self.baseline.interfaces.items():
                parent = spec.get("parent")
                if parent:
                    index.setdefault(parent, []).append(name)
            self._children = {key: tuple(values) for key, values in index.items()}
        return self._children

    def descendants(self, name):
        seen = set()
        pending = list(self.children().get(name, ()))
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.children().get(current, ()))
        positions = self.positions()
        return tuple(sorted(seen, key=positions.__getitem__))

    def exposed_names(self, context_type, expose_unimplemented, implementations):
        key = (context_type, bool(expose_unimplemented))
        cached = self._exposed.get(key)
        if cached is None:
            cached = tuple(
                name for name, spec in self.baseline.interfaces.items()
                if context_type in (spec.get("exposed_in") or ())
                and (expose_unimplemented or name in implementations)
            )
            self._exposed[key] = cached
        return cached

    def walk_parent_names(self, name):
        seen = set()
        while name and name not in seen:
            seen.add(name)
            yield name
            name = self.baseline.interfaces[name].get("parent")
