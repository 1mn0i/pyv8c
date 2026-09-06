"""Class config assembly: merge baseline members with real implementations.

``ClassConfigBuilder`` turns one interface's baseline spec plus its Python
class into the native ``ClassConfig`` wire shape: wrapped callbacks for
hand-written members, stubs for declared-but-unimplemented members, V8
template flags, constants, and constructor behaviour.
"""

from __future__ import annotations

import threading

from pyv8.flag import (
    CallbackType,
    FlagConstructor,
    FlagCrossOriginCheck,
    FlagLocation,
    FlagReceiverCheck,
    SideEffectType,
    V8Attribute,
)

from .config import apply_template_flags


def _location(value):
    return {
        "instance": FlagLocation.kInstance,
        "prototype": FlagLocation.kPrototype,
        "interface": FlagLocation.kInterface,
    }.get(value, FlagLocation.kPrototype)


def _side_effect(value):
    return {
        "HasNoSideEffect": SideEffectType.kHasNoSideEffect,
        "HasSideEffectToReceiver": SideEffectType.kHasSideEffectToReceiver,
    }.get(value, SideEffectType.kHasSideEffect)


def _receiver(value):
    return FlagReceiverCheck.kDoNotCheck if value == "DoNotCheck" else FlagReceiverCheck.kCheck


def _cross_origin(value):
    return FlagCrossOriginCheck.kDoNotCheck if value == "DoNotCheck" else FlagCrossOriginCheck.kCheck


def _property_flags(member):
    flags = str(member.get("flags") or "")
    result = V8Attribute.ReadOnly if member.get("setter") is None or "DontWrite" in flags else 0
    if "DontEnum" in flags:
        result |= V8Attribute.DontEnum
    if "DontDelete" in flags:
        result |= V8Attribute.DontDelete
    return result


# (baseline handler key, __v8_*__ method name) per interceptor family.
HANDLER_FAMILIES = (
    ("named", (
        ("get", "__v8_name_get__"), ("set", "__v8_name_set__"),
        ("query", "__v8_name_query__"), ("del", "__v8_name_del__"),
        ("enum", "__v8_name_enum__"),
    )),
    ("indexed", (
        ("get", "__v8_index_get__"), ("set", "__v8_index_set__"),
        ("query", "__v8_index_query__"), ("del", "__v8_index_del__"),
        ("enum", "__v8_index_enum__"),
    )),
)
CROSS_ORIGIN_FAMILIES = (
    ("named", (
        ("get", "__v8_cross_origin_name_get__"), ("set", "__v8_cross_origin_name_set__"),
        ("query", "__v8_cross_origin_name_query__"), ("del", "__v8_cross_origin_name_del__"),
        ("enum", "__v8_cross_origin_name_enum__"),
    )),
    ("indexed", (
        ("get", "__v8_cross_origin_index_get__"), ("set", "__v8_cross_origin_index_set__"),
        ("query", "__v8_cross_origin_index_query__"), ("del", "__v8_cross_origin_index_del__"),
        ("enum", "__v8_cross_origin_index_enum__"),
    )),
)


def own_configs(cls, config_name, public_names=()):
    configs = list(cls.__dict__.get(config_name, ()) or ())
    configured = {item[0] for item in configs}
    public_names = set(public_names)
    for base in cls.__mro__[1:]:
        for item in base.__dict__.get(config_name, ()) or ():
            if item[0] in public_names and item[0] not in configured:
                configs.append(item)
                configured.add(item[0])
    return configs


def declares_callback(cls, name):
    """True when the class or one of its bases defines ``name`` itself."""
    return any(name in base.__dict__ for base in cls.__mro__)


def raw_callback(cls, name):
    for base in cls.__mro__:
        if name in base.__dict__:
            raw = base.__dict__[name]
            return raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
    raise TypeError(f"callback {cls.__name__}.{name} is missing")


class ClassConfigBuilder:
    """Assemble per-interface class configs for one interface universe."""

    def __init__(self, universe, stubs, wrappers):
        self.universe = universe
        self.stubs = stubs
        self.wrappers = wrappers
        self._lock = threading.RLock()
        self._handler_cache = {}

    def declared_handlers(self, name):
        """The ``__v8_*__`` handler names the baseline declares for an interface.

        Only these handlers are installed on the V8 template, and only these
        receive a Python stub. Installing all twenty for every interface
        created tens of thousands of unused closures.
        """
        with self._lock:
            cached = self._handler_cache.get(name)
            if cached is not None:
                return cached
        template = self.universe.spec(name).get("template") or {}
        handlers = template.get("handlers") or {}
        cross = handlers.get("cross_origin") or {}
        cross_named = cross.get("named") or {}
        cross_indexed = cross.get("indexed") or {}
        enabled = []
        for family, mapping in HANDLER_FAMILIES:
            keys = handlers.get(family) or {}
            enabled.extend(name for key, name in mapping if key in keys)
        for family, mapping in CROSS_ORIGIN_FAMILIES:
            keys = cross.get(family) or {}
            enabled.extend(name for key, name in mapping if key in keys)
        if template.get("access_check") or cross_named or cross_indexed:
            enabled.append("__v8_cross_origin_check__")
        # Hand-written implementations may provide a callback that is not
        # represented in compact baseline metadata (for example an indexed
        # query used to expose a correct readonly descriptor). Preserve those
        # explicit callbacks instead of silently dropping them.
        cls = self.universe.class_for(name)
        for _family, mapping in HANDLER_FAMILIES + CROSS_ORIGIN_FAMILIES:
            for _key, method_name in mapping:
                if method_name not in enabled and any(method_name in base.__dict__ for base in cls.__mro__):
                    enabled.append(method_name)
        # Snapshot native interceptors need not become Python interceptors.
        # In particular Window uses a separate named-properties prototype.
        if getattr(cls, "__v8_disable_named_handlers__", False):
            named_handlers = {method for _key, method in HANDLER_FAMILIES[0][1]}
            enabled = [method for method in enabled if method not in named_handlers]
        result = tuple(enabled)
        with self._lock:
            existing = self._handler_cache.get(name)
            if existing is not None:
                return existing
            self._handler_cache[name] = result
            return result

    def member_configs(self, name, cls, realm=None):
        spec = self.universe.spec(name)
        direct_members = [member for _location_name, member in self.universe.own_members(name)]
        operation_names = {member.get("name") for member in direct_members if member.get("kind") == "operation"}
        attribute_names = {member.get("name") for member in direct_members if member.get("kind") == "attribute"}
        methods = own_configs(cls, "__v8_method__", operation_names)
        attributes = own_configs(cls, "__v8_attribute__", attribute_names)
        callbacks = {}
        configured_methods = set()
        configured_attributes = set()
        wrapped_methods = []
        for config in methods:
            public_name, callback_name = config[0], config[1]
            static = config[4] == FlagLocation.kInterface
            plan = self.universe.binding_plan(name, public_name)
            wrapper = self.wrappers.method(cls, name, public_name, callback_name, plan, static=static, realm=realm)
            callbacks[wrapper.__name__] = wrapper
            item = list(config)
            item[1] = wrapper.__name__
            wrapped_methods.append(tuple(item))
            configured_methods.add(public_name)
        wrapped_attributes = []
        for config in attributes:
            public_name, _getter_name, setter_name = config[0], config[1], config[2]
            item = list(config)
            if setter_name:
                plan = self.universe.binding_plan(name, public_name, "attribute")
                wrapper = self.wrappers.setter(cls, name, public_name, setter_name, plan)
                callbacks[wrapper.__name__] = wrapper
                item[2] = wrapper.__name__
            wrapped_attributes.append(tuple(item))
            configured_attributes.add(public_name)
        for location_name in ("instance", "prototype", "interface"):
            for member in (spec.get("members") or {}).get(location_name, ()) or ():
                member_name = member.get("name")
                if not member_name:
                    continue
                if member.get("kind") == "operation" and member_name not in configured_methods:
                    plan = self.universe.binding_plan(name, member_name)
                    callback = self.stubs.method(
                        name, member_name, plan, static=location_name == "interface",
                    )
                    callbacks[callback.__name__] = callback
                    wrapped_methods.append((
                        member_name, callback.__name__, int(member.get("length") or 0),
                        CallbackType.kPy, _location(location_name), _property_flags(member),
                        _receiver(member.get("receiver")), _cross_origin(member.get("cross_origin")),
                        _side_effect(member.get("side_effect")),
                    ))
                    configured_methods.add(member_name)
                elif member.get("kind") == "attribute" and member_name not in configured_attributes:
                    getter = self.stubs.getter(name, member_name)
                    callbacks[getter.__name__] = getter
                    setter_name = None
                    if member.get("setter"):
                        plan = self.universe.binding_plan(name, member_name, "attribute")
                        setter = self.stubs.setter(name, member_name, plan)
                        callbacks[setter.__name__] = setter
                        setter_name = setter.__name__
                    # Native ClassConfig consumes a 10-item attribute tuple;
                    # the duplicated cross-origin slot is part of the wire shape.
                    wrapped_attributes.append((
                        member_name, getter.__name__, setter_name, CallbackType.kPy,
                        _location(location_name), _property_flags(member),
                        _receiver(member.get("receiver")), _cross_origin(member.get("cross_origin")),
                        _cross_origin(member.get("cross_origin")), _side_effect(member.get("side_effect")),
                    ))
                    configured_attributes.add(member_name)
        # Add safe Python stubs so ClassConfig can install the V8 handlers the
        # baseline actually declares. Interfaces without handlers get none.
        for method_name in self.declared_handlers(name):
            if not any(method_name in base.__dict__ for base in cls.__mro__):
                callbacks[method_name] = self.stubs.handler(name, method_name)
        callback_owner = type(f"_{name}Callbacks_{id(self):x}", (cls,), callbacks)
        return tuple(wrapped_methods), tuple(wrapped_attributes), callback_owner

    def build(self, name, cache, realm=None):
        cls = self.universe.class_for(name)
        if cls in cache:
            return cache[cls]
        spec = self.universe.spec(name)
        methods, attributes, callback_owner = self.member_configs(name, cls, realm)
        parent_name = spec.get("parent")
        config = {
            "__v8_method__": methods,
            "__v8_attribute__": attributes,
            "__v8_callback_owner__": callback_owner,
            "__v8_parent_class__": self.universe.class_for(parent_name) if parent_name else object,
            "__v8_to_string_tag__": spec.get("tag") or name,
        }
        array_flags = int(getattr(cls, "__v8_array__", 0) or 0)
        if array_flags:
            config["__v8_array__"] = array_flags
        for method_name in self.declared_handlers(name):
            config[method_name] = True
        template = spec.get("template") or {}
        apply_template_flags(config, template)
        # A hand-written legacy caller (document.all) must win over the stub;
        # only interfaces without a real __call__ need the no-op handler.
        if template.get("call_handler") and not declares_callback(cls, "__call__"):
            config["__call__"] = self.stubs.call_handler(name)
        constants = []
        constant_names = set()
        for constant in spec.get("constants") or ():
            if constant.get("name") in constant_names:
                continue
            if constant.get("resolved") and isinstance(constant.get("value"), int):
                constants.append((constant.get("name"), constant.get("value")))
                constant_names.add(constant.get("name"))
        if not constants:
            for _loc, member in self.universe.own_members(name):
                if member.get("kind") == "constant" and isinstance(member.get("value"), int) and member.get("name") not in constant_names:
                    constants.append((member.get("name"), member.get("value")))
                    constant_names.add(member.get("name"))
        if constants:
            constants = [item for item in constants if item[0] not in cls.__dict__]
            if constants:
                config["__v8_constants_value__"] = tuple(constants)
        constructor = spec.get("constructor") or {}
        length = constructor.get("length")
        if length is None:
            plan = self.universe.binding_plan(name, "constructor", "constructor")
            length = plan.signature.required_count() if plan.resolved else None
        if length is not None:
            config["__v8_length__"] = int(length)
        if constructor.get("allow_new") is not None or constructor.get("allow_call") is not None:
            behavior = FlagConstructor.kDontAll
            if constructor.get("allow_new"):
                behavior |= FlagConstructor.kNew
            if constructor.get("allow_call"):
                behavior |= FlagConstructor.kCall
            config["__v8_constructor__"] = behavior
        stub = bool(cls.__dict__.get("__v8_idl_stub__", False))
        if constructor.get("allow_new") or constructor.get("allow_call"):
            config["__v8_constructor_adapter__"] = self.wrappers.constructor_adapter(name, stub, realm)
        if stub:
            config.update({
                "__v8_length__": int(length or 0),
                "__v8_constructor__": config.get(
                    "__v8_constructor__",
                    FlagConstructor.kNew if (constructor.get("allow_new") or constructor.get("allow_call")) else FlagConstructor.kDontAll,
                ),
                "__v8_has_constructor__": 1,
            })
        cache[cls] = config
        return config
