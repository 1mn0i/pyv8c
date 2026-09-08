"""Reusable callback-parameter caching and generated binding wrappers.

``SignatureCache`` remembers Python callback parameter names so wrapper
generation does not re-inspect signatures.  ``BindingWrapperFactory``
builds the argument-validating wrappers installed between V8 and the
hand-written implementation callbacks of one interface universe.
"""

from __future__ import annotations

import inspect

from .webidl import validate_arguments


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
    """Build wrappers that validate arguments before calling real callbacks."""

    def __init__(self, universe):
        self.universe = universe
        self._parameter_cache = SignatureCache()

    def _parameters_for(self, callback):
        return self._parameter_cache.names(callback)

    def _reject_conversion(self, receiver, exc):
        # Resolve the realm through the core protocol so bindings never depends
        # on runtime internals such as Page privates.
        from ..foundation.realm import realm_of
        realm = realm_of(receiver)
        if realm is None:
            raise exc
        promise, _resolve, reject = realm.make_promise()
        realm.post_task(lambda: reject(realm.js_error_value(exc)))
        return promise

    def method(self, cls, interface_name, public_name, callback_name, plan, *, static, realm=None):
        raw = self._raw_callback(cls, callback_name)
        parameters = self._parameters_for(raw)
        inject_realm = static and realm is not None and parameters[:1] == ("_realm",)

        def callback(*args):
            receiver = None if static else args[0]
            values = args if static else args[1:]
            try:
                converted = validate_arguments(plan, values)
            except Exception as exc:
                if plan.error_mode == "reject-promise" and receiver is not None:
                    return self._reject_conversion(receiver, exc)
                raise
            if inject_realm:
                return raw(realm, *converted)
            return raw(*converted) if static else raw(receiver, *converted)

        callback.__name__ = f"_pyv8c_binding_{interface_name}_{public_name}"
        return callback

    def setter(self, cls, interface_name, public_name, callback_name, plan):
        raw = self._raw_callback(cls, callback_name)

        def setter(receiver, value):
            converted = validate_arguments(plan, (value,))
            return raw(receiver, *converted)

        setter.__name__ = f"_pyv8c_binding_set_{interface_name}_{public_name}"
        return setter

    def constructor_adapter(self, interface_name, stub, realm=None):
        plan = self.universe.binding_plan(interface_name, "constructor", "constructor")

        implementation = self.universe.class_for(interface_name)
        inject_realm = "_realm" in self._parameters_for(implementation)

        def construct(cls, args, kwargs):
            converted = validate_arguments(plan, tuple(args)) if plan.resolved else tuple(args)
            options = dict(kwargs)
            if realm is not None and inject_realm:
                options["_realm"] = realm
            return cls(*converted, **options)

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
