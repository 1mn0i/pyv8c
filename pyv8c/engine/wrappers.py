"""Creation-time callback inspection and realm injection; no argument validation."""

from __future__ import annotations

import inspect


class SignatureCache:
    """Cache Python callback parameter names for one interface universe."""

    def __init__(self):
        self._values = {}

    def names(self, callback):
        names = self._values.get(callback)
        if names is None:
            names = tuple(inspect.signature(callback).parameters)
            self._values[callback] = names
        return names


class BindingWrapperFactory:
    """Bind implementation callbacks directly, injecting a realm only when requested."""

    def __init__(self, universe):
        self.universe = universe
        self._parameter_cache = SignatureCache()

    def _parameters_for(self, callback):
        return self._parameter_cache.names(callback)

    def method(self, cls, interface_name, public_name, callback_name, *, static, realm=None):
        raw = self._raw_callback(cls, callback_name)
        parameters = self._parameters_for(raw)
        inject_realm = static and realm is not None and parameters[:1] == ("_realm",)

        if not inject_realm:
            return raw

        def callback(*args):
            return raw(realm, *args)

        callback.__name__ = f"_pyv8c_binding_{interface_name}_{public_name}"
        return callback

    def setter(self, cls, interface_name, public_name, callback_name):
        return self._raw_callback(cls, callback_name)

    def constructor_adapter(self, interface_name, stub, realm=None):
        implementation = self.universe.class_for(interface_name)
        inject_realm = "_realm" in self._parameters_for(implementation)

        def construct(cls, args, kwargs):
            options = dict(kwargs)
            if realm is not None and inject_realm:
                options["_realm"] = realm
            return cls(*args, **options)

        construct.__name__ = f"_pyv8c_construct_{interface_name}"
        if stub:
            construct.__v8_idl_stub__ = True
        return construct

    @staticmethod
    def _raw_callback(cls, name):
        if callable(name):
            return name
        for base in cls.__mro__:
            if name in base.__dict__:
                raw = base.__dict__[name]
                return raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
        raise TypeError(f"callback {cls.__name__}.{name} is missing")
