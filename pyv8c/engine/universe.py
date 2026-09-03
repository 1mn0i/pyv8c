"""Per-Browser interface universe: materialization, stubs and binding sessions.

Baseline file I/O and the two-level disk cache live in :mod:`.baseline`;
this module keeps the per-Browser :class:`InterfaceUniverse` (class
materialization, binding plans, generated API shells) and the realm-local
:class:`BindingSession`.
"""

from __future__ import annotations

from typing import Any

import pyv8
from loguru import logger
from pyv8.flag import (
    CallbackType,
    FlagConstructor,
    FlagCrossOriginCheck,
    FlagLocation,
    FlagReceiverCheck,
    SideEffectType,
    V8Attribute,
)

from .baseline import (
    DEFAULT_BASELINE_PATH,
    BaselineRepository,
    BaselineSnapshot,
)
from .webidl import BindingPlan, compile_binding, validate_arguments
from .catalog import InterfaceCatalog
from .config import apply_template_flags
from .wrappers import SignatureCache

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
        self._classes: dict[str, type] = {}
        self._plans: dict[tuple[str, str, str], BindingPlan] = {}
        self._catalog = InterfaceCatalog(baseline)
        self._exposure_names = {}
        self._handler_cache = {}
        self._parameter_cache = SignatureCache()

    def _log(self, message, *args):
        if self.stub_logging not in ("off", "none", "disabled"):
            logger.info(message, *args)

    def _spec(self, name):
        try:
            return self.baseline.interfaces[name]
        except KeyError as exc:
            raise TypeError(f"interface {name!r} is missing from Chromium interface baseline") from exc

    def _interface_positions(self):
        """Stable document order, used to keep index traversals deterministic."""
        return self._catalog.positions()

    def _children_of_index(self):
        """parent -> direct children, built once from the baseline."""
        return self._catalog.children()

    def _descendants(self, name):
        """All transitive descendants of ``name``, in document order.

        Blink installs some shared members on concrete descendants, so member
        lookup has to search downwards as well as up the parent chain.
        """
        return self._catalog.descendants(name)

    def _own_members(self, interface_name):
        spec = self._spec(interface_name)
        for location in ("instance", "prototype", "interface"):
            for member in (spec.get("members") or {}).get(location, ()) or ():
                yield location, member

    def interface_names(self, context_type="Window"):
        """Return the stable global name table without materializing classes."""
        if context_type not in self._exposure_names:
            self._exposure_names[context_type] = tuple(sorted(self.exposed_names(context_type)))
        return self._exposure_names[context_type]

    def eager_names(self, context_type="Window"):
        exposed = set(self.interface_names(context_type))
        names = set(name for name in self.eager_interfaces if name in exposed or name == "Window")
        return tuple(name for name in self.interface_names(context_type) if name in names)

    def _member(self, interface_name, public_name, kind):
        seen, current = set(), interface_name
        while current and current not in seen:
            seen.add(current)
            candidates = [
                (location, member) for location, member in self._own_members(current)
                if member.get("name") == public_name and member.get("kind") == kind
            ]
            if candidates:
                candidates.sort(key=lambda item: (
                    item[1].get("world") not in (None, "AllWorlds", "MainWorld"),
                    (item[1].get("signature") or {}).get("status") == "unresolved",
                ))
                return current, candidates[0][0], candidates[0][1]
            current = self._spec(current).get("parent")
        # Blink sometimes installs WorkerGlobalScope members in a concrete
        # descendant binding table. Keep this lookup explicit and deterministic.
        for candidate_name in self._descendants(interface_name):
            for location, member in self._own_members(candidate_name):
                if member.get("name") == public_name and member.get("kind") == kind:
                    return candidate_name, location, member
        return None

    # (baseline handler key, __v8_*__ method name) per interceptor family.
    _HANDLER_FAMILIES = (
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
    _CROSS_ORIGIN_FAMILIES = (
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

    def _declared_handlers(self, name):
        """The ``__v8_*__`` handler names the baseline declares for an interface.

        Only these handlers are installed on the V8 template, and only these
        receive a Python stub. Installing all twenty for every interface
        created tens of thousands of unused closures.
        """
        cached = self._handler_cache.get(name)
        if cached is not None:
            return cached
        template = self._spec(name).get("template") or {}
        handlers = template.get("handlers") or {}
        cross = handlers.get("cross_origin") or {}
        cross_named = cross.get("named") or {}
        cross_indexed = cross.get("indexed") or {}
        enabled = []
        for family, mapping in self._HANDLER_FAMILIES:
            keys = handlers.get(family) or {}
            enabled.extend(name for key, name in mapping if key in keys)
        for family, mapping in self._CROSS_ORIGIN_FAMILIES:
            keys = cross.get(family) or {}
            enabled.extend(name for key, name in mapping if key in keys)
        if template.get("access_check") or cross_named or cross_indexed:
            enabled.append("__v8_cross_origin_check__")
        # Hand-written implementations may provide a callback that is not
        # represented in compact baseline metadata (for example an indexed
        # query used to expose a correct readonly descriptor). Preserve those
        # explicit callbacks instead of silently dropping them.
        cls = self.class_for(name)
        for _family, mapping in self._HANDLER_FAMILIES + self._CROSS_ORIGIN_FAMILIES:
            for _key, method_name in mapping:
                if method_name not in enabled and any(method_name in base.__dict__ for base in cls.__mro__):
                    enabled.append(method_name)
        result = tuple(enabled)
        self._handler_cache[name] = result
        return result

    def binding_plan(self, interface_name, public_name, kind="operation"):
        key = (interface_name, public_name, kind)
        if key in self._plans:
            return self._plans[key]
        if kind == "constructor":
            signature = (self._spec(interface_name).get("constructor") or {}).get("signature")
        else:
            found = self._member(interface_name, public_name, "attribute" if kind == "attribute" else "operation")
            if found is None:
                raise TypeError(f"{interface_name}.{public_name} is not present in Chromium interface baseline")
            _owner, _location_name, member = found
            signature = member.get("setter_signature") if kind == "attribute" else member.get("signature")
        plan = compile_binding(interface_name, public_name, kind, signature)
        self._plans[key] = plan
        return plan

    @staticmethod
    def _mark_direct(callback, *, stub=False):
        callback.__v8_direct_callback__ = True
        if stub:
            callback.__v8_idl_stub__ = True
        return callback

    def _stub_method(self, interface_name, member_name, plan, *, static=False):
        def callback(*args):
            values = args if static else args[1:]
            if plan.resolved:
                validate_arguments(plan, values)
            self._log("pyv8c: {}.{}() 待实现", interface_name, member_name)
            return pyv8.Undefined
        callback.__name__ = f"_pyv8c_stub_method_{interface_name}_{member_name}"
        return self._mark_direct(callback, stub=True)

    def _stub_getter(self, interface_name, member_name):
        def getter(*_args):
            self._log("pyv8c: {}.{} getter 待实现", interface_name, member_name)
            return pyv8.Undefined
        getter.__name__ = f"_pyv8c_stub_get_{interface_name}_{member_name}"
        return self._mark_direct(getter, stub=True)

    def _stub_setter(self, interface_name, member_name, plan):
        def setter(*args):
            values = args[1:] if len(args) > 1 else args
            if plan.resolved:
                validate_arguments(plan, values)
            self._log("pyv8c: {}.{} setter 待实现", interface_name, member_name)
        setter.__name__ = f"_pyv8c_stub_set_{interface_name}_{member_name}"
        return self._mark_direct(setter, stub=True)

    def _stub_constructor(self, interface_name):
        def constructor(_self, *_args, **_kwargs):
            self._log("pyv8c: {} constructor 待实现", interface_name)
        constructor.__name__ = interface_name
        constructor.__v8_idl_stub__ = True
        return constructor

    def _stub_call_handler(self, interface_name):
        def call_handler(*_args):
            return pyv8.Undefined
        call_handler.__name__ = f"_pyv8c_stub_call_{interface_name}"
        return self._mark_direct(call_handler, stub=True)

    def _stub_handler(self, interface_name, method_name):
        def handler(*args):
            # Returning None tells V8 that the interceptor did not handle it.
            if method_name.endswith("enum__"):
                return ()
            if method_name.endswith("query__") or method_name.endswith("del__"):
                return None
            return None
        handler.__name__ = f"_pyv8c_stub_{method_name}_{interface_name}"
        return self._mark_direct(handler, stub=True)

    def class_for(self, name):
        if name in self._classes:
            return self._classes[name]
        spec = self._spec(name)
        parent_name = spec.get("parent")
        parent = self.class_for(parent_name) if parent_name else object
        implementation = self.registry.implementations.get(name)
        if implementation is None:
            cls = type(name, (parent,), {
                "__module__": "pyv8c.engine.generated",
                "__init__": self._stub_constructor(name),
                "__idl_name__": name,
                "__idl_parent__": parent_name,
                "__v8_idl_stub__": True,
            })
        else:
            cls = implementation
        self._classes[name] = cls
        return cls

    @staticmethod
    def _own_configs(cls, config_name, public_names=()):
        configs = list(cls.__dict__.get(config_name, ()) or ())
        configured = {item[0] for item in configs}
        public_names = set(public_names)
        for base in cls.__mro__[1:]:
            for item in base.__dict__.get(config_name, ()) or ():
                if item[0] in public_names and item[0] not in configured:
                    configs.append(item)
                    configured.add(item[0])
        return configs

    @staticmethod
    def _declares_callback(cls, name):
        """True when the class or one of its bases defines ``name`` itself."""
        return any(name in base.__dict__ for base in cls.__mro__)

    @staticmethod
    def _raw_callback(cls, name):
        for base in cls.__mro__:
            if name in base.__dict__:
                raw = base.__dict__[name]
                return raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
        raise TypeError(f"callback {cls.__name__}.{name} is missing")

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

    def _method_wrapper(self, cls, interface_name, public_name, callback_name, plan, *, static, realm=None):
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
        return self._mark_direct(callback)

    def _setter_wrapper(self, cls, interface_name, public_name, callback_name, plan):
        raw = self._raw_callback(cls, callback_name)

        def setter(receiver, value):
            converted = validate_arguments(plan, (value,))
            return raw(receiver, *converted)

        setter.__name__ = f"_pyv8c_binding_set_{interface_name}_{public_name}"
        return self._mark_direct(setter)

    def _constructor_adapter(self, interface_name, stub, realm=None):
        plan = self.binding_plan(interface_name, "constructor", "constructor")

        def construct(cls, args, kwargs):
            converted = validate_arguments(plan, tuple(args)) if plan.resolved else tuple(args)
            options = dict(kwargs)
            if realm is not None and "_realm" in self._parameters_for(cls):
                options["_realm"] = realm
            return cls(*converted, **options)

        construct.__name__ = f"_pyv8c_construct_{interface_name}"
        if stub:
            construct.__v8_idl_stub__ = True
        return construct

    def _member_configs(self, name, cls, realm=None):
        spec = self._spec(name)
        direct_members = [member for _location_name, member in self._own_members(name)]
        operation_names = {member.get("name") for member in direct_members if member.get("kind") == "operation"}
        attribute_names = {member.get("name") for member in direct_members if member.get("kind") == "attribute"}
        methods = self._own_configs(cls, "__v8_method__", operation_names)
        attributes = self._own_configs(cls, "__v8_attribute__", attribute_names)
        callbacks = {}
        configured_methods = set()
        configured_attributes = set()
        wrapped_methods = []
        for config in methods:
            public_name, callback_name = config[0], config[1]
            static = config[4] == FlagLocation.kInterface
            plan = self.binding_plan(name, public_name)
            wrapper = self._method_wrapper(cls, name, public_name, callback_name, plan, static=static, realm=realm)
            callbacks[wrapper.__name__] = wrapper
            item = list(config)
            item[1] = wrapper.__name__
            wrapped_methods.append(tuple(item))
            configured_methods.add(public_name)
        wrapped_attributes = []
        for config in attributes:
            public_name, getter_name, setter_name = config[0], config[1], config[2]
            item = list(config)
            if setter_name:
                plan = self.binding_plan(name, public_name, "attribute")
                wrapper = self._setter_wrapper(cls, name, public_name, setter_name, plan)
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
                    plan = self.binding_plan(name, member_name)
                    callback = self._stub_method(
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
                    getter = self._stub_getter(name, member_name)
                    callbacks[getter.__name__] = getter
                    setter_name = None
                    if member.get("setter"):
                        plan = self.binding_plan(name, member_name, "attribute")
                        setter = self._stub_setter(name, member_name, plan)
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
        for method_name in self._declared_handlers(name):
            if not any(method_name in base.__dict__ for base in cls.__mro__):
                callbacks[method_name] = self._stub_handler(name, method_name)
        callback_owner = type(f"_{name}Callbacks_{id(self):x}", (cls,), callbacks)
        return tuple(wrapped_methods), tuple(wrapped_attributes), callback_owner

    def class_config_for(self, name):
        """Build a config for inspection outside of any realm.

        Deliberately uncached. Only a :class:`BindingSession` owns configs,
        because member wrappers may capture the realm they were built for and
        sharing them across realms would leak one realm into another.
        """
        return self._class_config_for(name, {}, None)

    def _class_config_for(self, name, cache, realm):
        cls = self.class_for(name)
        if cls in cache:
            return cache[cls]
        spec = self._spec(name)
        methods, attributes, callback_owner = self._member_configs(name, cls, realm)
        parent_name = spec.get("parent")
        config = {
            "__v8_method__": methods,
            "__v8_attribute__": attributes,
            "__v8_callback_owner__": callback_owner,
            "__v8_parent_class__": self.class_for(parent_name) if parent_name else object,
            "__v8_to_string_tag__": spec.get("tag") or name,
        }
        array_flags = int(getattr(cls, "__v8_array__", 0) or 0)
        if array_flags:
            config["__v8_array__"] = array_flags
        for method_name in self._declared_handlers(name):
            config[method_name] = True
        template = spec.get("template") or {}
        apply_template_flags(config, template)
        # A hand-written legacy caller (document.all) must win over the stub;
        # only interfaces without a real __call__ need the no-op handler.
        if template.get("call_handler") and not self._declares_callback(cls, "__call__"):
            config["__call__"] = self._stub_call_handler(name)
        constants = []
        constant_names = set()
        for constant in spec.get("constants") or ():
            if constant.get("name") in constant_names:
                continue
            if constant.get("resolved") and isinstance(constant.get("value"), int):
                constants.append((constant.get("name"), constant.get("value")))
                constant_names.add(constant.get("name"))
        if not constants:
            for _loc, member in self._own_members(name):
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
            plan = self.binding_plan(name, "constructor", "constructor")
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
            config["__v8_constructor_adapter__"] = self._constructor_adapter(name, stub, realm)
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

    def _walk_parent_names(self, name):
        yield from self._catalog.walk_parent_names(name)

    def exposed_names(self, context_type):
        return list(self._catalog.exposed_names(
            context_type, self.expose_unimplemented, self.registry.implementations,
        ))

    def _resolve_names(self, context_type, roots=()):
        """Interface names (including ancestors) a context_type must install."""
        names = set(
            self.eager_names(context_type)
            if self.lazy_interfaces else self.exposed_names(context_type)
        )
        names.update(root for root in roots if isinstance(root, str))
        for name in tuple(names):
            names.update(self._walk_parent_names(name))
        return names

    def _class_configs(self, context_type, *, roots=(), cache, realm):
        names = self._resolve_names(context_type, roots)
        return {self.class_for(name): self._class_config_for(name, cache, realm) for name in names}

    def binding_session(self, realm=None):
        return BindingSession(self, realm)

    def validate(self):
        self.registry.validate(self.baseline.interfaces)
        for name, interface_spec in self.registry.specs.items():
            cls = self.registry.implementations[name]
            direct_members = [member for _location_name, member in self._own_members(name)]
            operation_names = {member.get("name") for member in direct_members if member.get("kind") == "operation"}
            attribute_names = {member.get("name") for member in direct_members if member.get("kind") == "attribute"}
            for config in self._own_configs(cls, "__v8_method__", operation_names):
                plan = self.binding_plan(name, config[0])
                if not plan.resolved:
                    raise TypeError(
                        f"{name}.{config[0]} has unresolved Web IDL signature"
                        + (f": {plan.source_evidence}" if plan.source_evidence else "")
                    )
            for config in self._own_configs(cls, "__v8_attribute__", attribute_names):
                if config[2]:
                    plan = self.binding_plan(name, config[0], "attribute")
                    if not plan.resolved:
                        raise TypeError(f"{name}.{config[0]} setter has unresolved Web IDL signature")
            if interface_spec.constructible:
                plan = self.binding_plan(name, "constructor", "constructor")
                if not plan.resolved:
                    raise TypeError(f"{name} constructor has unresolved Web IDL signature")


class BindingSession:
    """Context-local class configs and callback owners for one V8 realm."""

    def __init__(self, universe: InterfaceUniverse, realm=None):
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
        chain = list(self.universe._walk_parent_names(name))
        for interface_name in reversed(chain):
            cls = self.class_for(interface_name)
            config = self.universe._class_config_for(interface_name, self._configs, self.realm)
            self._materialized.add(interface_name)
        return self.class_for(name)

    def class_config_for(self, name):
        self.materialize(name)
        return self._configs[self.class_for(name)]

    def class_configs(self, context_type, *, roots=()):
        names = self.universe._resolve_names(context_type, roots)
        configs = self.universe._class_configs(
            context_type, roots=roots, cache=self._configs, realm=self.realm,
        )
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
