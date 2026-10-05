"""Turn baseline declarations and hand-written classes into native IdlSpecs.

The one module that knows both sides: it decodes a member's shorthand into the flags the
binding wants, compiles the realm-independent structure once, fills undeclared members
with safe shells, and rebinds only the callbacks that need a realm.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass

import pyv8
from pyv8 import AttributeSpec, ConstantSpec, IdlSpec, MethodSpec
from pyv8.flag import (
    FlagCrossOriginCheck,
    FlagLocation,
    FlagReceiverCheck,
    SideEffectType,
    V8Attribute,
)

from ..foundation.logging import logger


# (baseline handler key, __v8_*__ method name) per interceptor family.
HANDLER_FAMILIES = (
    (
        "named",
        (
            ("get", "__v8_name_get__"),
            ("set", "__v8_name_set__"),
            ("query", "__v8_name_query__"),
            ("del", "__v8_name_del__"),
            ("enum", "__v8_name_enum__"),
        ),
    ),
    (
        "indexed",
        (
            ("get", "__v8_index_get__"),
            ("set", "__v8_index_set__"),
            ("query", "__v8_index_query__"),
            ("del", "__v8_index_del__"),
            ("enum", "__v8_index_enum__"),
        ),
    ),
)
CROSS_ORIGIN_FAMILIES = (
    (
        "named",
        (
            ("get", "__v8_cross_origin_name_get__"),
            ("set", "__v8_cross_origin_name_set__"),
            ("query", "__v8_cross_origin_name_query__"),
            ("del", "__v8_cross_origin_name_del__"),
            ("enum", "__v8_cross_origin_name_enum__"),
        ),
    ),
    (
        "indexed",
        (
            ("get", "__v8_cross_origin_index_get__"),
            ("set", "__v8_cross_origin_index_set__"),
            ("query", "__v8_cross_origin_index_query__"),
            ("del", "__v8_cross_origin_index_del__"),
            ("enum", "__v8_cross_origin_index_enum__"),
        ),
    ),
)

#: Every named/indexed callback, plain and cross-origin, that may be stubbed when the baseline declares it.
HANDLER_CALLBACKS = frozenset(
    callback for _family, mapping in HANDLER_FAMILIES + CROSS_ORIGIN_FAMILIES
    for _key, callback in mapping
)


# Template fields the baseline states that map straight onto the binding's flags.
TEMPLATE_FLAGS = (
    ("immutable_proto", "immutable"),
    ("undetectable", "undetectable"),
    ("global_this", "global_this"),
    ("as_object", "as_object"),
    ("unconstructable", "unconstructable"),
)

_LOCATION_FLAGS = {
    "instance": FlagLocation.kInstance,
    "prototype": FlagLocation.kPrototype,
    "interface": FlagLocation.kInterface,
}
_SIDE_EFFECTS = {
    "HasNoEffect": SideEffectType.kHasNoSideEffect,
    "HasNoSideEffect": SideEffectType.kHasNoSideEffect,
    "HasSideEffectToReceiver": SideEffectType.kHasSideEffectToReceiver,
}
_PROPERTY_FLAGS = (
    ("ReadOnly", V8Attribute.ReadOnly),
    ("DontWrite", V8Attribute.ReadOnly),
    ("DontEnum", V8Attribute.DontEnum),
    ("DontDelete", V8Attribute.DontDelete),
)


def property_flags(member) -> int:
    """The property attributes of one member.

    A compact snapshot states the mask; an older one only states the flag text.
    """
    if member.property_flags is not None:
        return member.property_flags
    result = V8Attribute.kNone
    for name, bit in _PROPERTY_FLAGS:
        if name in member.flags:
            result |= bit
    return result


def member_flags(member) -> dict:
    """The flags every member spec carries, decoded from the member's shorthand."""
    return dict(
        location=_LOCATION_FLAGS.get(member.location, FlagLocation.kPrototype),
        property_flags=property_flags(member),
        receiver_check=(FlagReceiverCheck.kDoNotCheck if member.receiver == "DoNotCheck"
                        else FlagReceiverCheck.kCheck),
        side_effect=_SIDE_EFFECTS.get(member.side_effect, SideEffectType.kHasSideEffect),
    )


def cross_origin_check(member) -> int:
    """Whether one member is guarded by the cross-origin access check."""
    return (FlagCrossOriginCheck.kDoNotCheck if member.cross_origin == "DoNotCheck"
            else FlagCrossOriginCheck.kCheck)


def apply_template_flags(config, template):
    """The interface-wide flags one baseline template states."""
    for source, target in TEMPLATE_FLAGS:
        if template.get(source):
            config[target] = 1 if target == "immutable" else True
    # ``array`` marks a Web IDL indexed collection; the bridge shares FlagArrayProto's bit layout.
    if template.get("array") and not config.get("array"):
        config["array"] = 1
    return config


def mark_stub(callback):
    """Mark generated placeholders for Python diagnostics only."""
    callback.__v8_idl_stub__ = True
    return callback


class StubFactory:
    """Build the safe, logging shells used for unimplemented members."""

    #: The level that reports placeholder handlers, which run on ordinary property probes.
    HANDLER_LEVEL = "verbose"

    def __init__(self, stub_logging="off"):
        self.stub_logging = str(stub_logging or "off").lower()
        self.reports_handlers = self.stub_logging == self.HANDLER_LEVEL
        #: A reporting handler speaks once per interface and callback, not per probe.
        self._reported = set()

    def _log(self, message, *args):
        if self.stub_logging != "off":
            logger.info(message, *args)

    def method(self, interface_name, member_name, *, static=False):
        def callback(*args):
            self._log("pyv8c: {}.{}() 待实现", interface_name, member_name)
            return pyv8.Undefined
        callback.__name__ = f"_pyv8c_stub_method_{interface_name}_{member_name}"
        return mark_stub(callback)

    def getter(self, interface_name, member_name):
        def getter(*_args):
            self._log("pyv8c: {}.{} getter 待实现", interface_name, member_name)
            return pyv8.Undefined
        getter.__name__ = f"_pyv8c_stub_get_{interface_name}_{member_name}"
        return mark_stub(getter)

    def setter(self, interface_name, member_name):
        def setter(*args):
            self._log("pyv8c: {}.{} setter 待实现", interface_name, member_name)
        setter.__name__ = f"_pyv8c_stub_set_{interface_name}_{member_name}"
        return mark_stub(setter)

    def constructor(self, interface_name):
        def constructor(_self, *_args, **_kwargs):
            self._log("pyv8c: {} constructor 待实现", interface_name)
        constructor.__name__ = interface_name
        constructor.__v8_idl_stub__ = True
        return constructor

    def call_handler(self, interface_name):
        def call_handler(*_args):
            return pyv8.Undefined
        call_handler.__name__ = f"_pyv8c_stub_call_{interface_name}"
        return mark_stub(call_handler)

    def handler(self, interface_name, method_name):
        def handler(*args):
            reported = (interface_name, method_name)
            if self.reports_handlers and reported not in self._reported:
                self._reported.add(reported)
                self._log("pyv8c: {}.{} 待实现", interface_name, method_name)
            # None tells V8 the interceptor skipped it; enum interceptors report an empty tuple.
            if method_name.endswith("enum__"):
                return ()
            return None
        handler.__name__ = f"_pyv8c_stub_{method_name}_{interface_name}"
        return mark_stub(handler)


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


@dataclass(frozen=True)
class CompiledInterface:
    spec: IdlSpec
    realm_methods: tuple
    realm_constructor: bool


class InterfaceCompiler:
    """Compile one interface universe's classes, once each."""

    def __init__(self, universe, *, stub_logging="off", lock=None):
        self.universe = universe
        self.stubs = StubFactory(stub_logging)
        self.wrappers = BindingWrapperFactory(universe)
        # ``None`` when nothing is hooked, so the member loop pays a check, not a lookup.
        self.overrides = getattr(universe, "overrides", None) or None
        self._compiled = {}
        # The universe shares its lock: a compile reaches back into it for classes.
        self._lock = lock or threading.RLock()

    def _fills_stub(self, callback_name, registration):
        """Whether a handler the class does not declare is filled with a stub.

        Every named/indexed callback the baseline declares is filled, so a forgotten
        handler reports itself instead of dropping the interceptor; the cross-origin
        access check is excluded, since a placeholder there would deny guarded reads.
        """
        return callback_name in HANDLER_CALLBACKS or registration is None

    def _members(self, cls, parent, field, public_names):
        members = {}
        inherited = False
        for base in cls.__mro__:
            inherited = inherited or base is parent
            declaration = base.__dict__.get("__v8_idl_spec__")
            if declaration is None:
                continue
            own_names = set()
            for member in getattr(declaration, field):
                if member.name in own_names:
                    raise TypeError(f"{base.__name__} declares duplicate {field}: {member.name}")
                own_names.add(member.name)
                if not inherited or member.name in public_names:
                    members.setdefault(member.name, member)
        return members

    def compile(self, name):
        with self._lock:
            if name not in self._compiled:
                self._compiled[name] = self._compile(name)
            return self._compiled[name]

    def _compile(self, name):
        universe = self.universe
        cls = universe.class_for(name)
        interface = universe.table.interface(name)
        parent_name = interface.parent
        parent = universe.class_for(parent_name) if parent_name else object
        implementation = universe.runtime.interface(name)
        registration = getattr(implementation, "__idl_spec__", None)
        if registration is not None:
            if registration.parent and registration.parent not in universe.runtime.catalog.interfaces:
                raise TypeError(f"{name} refers to unknown parent {registration.parent}")
            if parent_name is not None and registration.parent != parent_name:
                if not universe.report_mismatch(
                        f"{name} parent {registration.parent!r} is not {parent_name!r} in the interface baseline"):
                    raise TypeError(f"{name} parent does not match baseline")
            if registration.parent and universe.class_for(registration.parent) not in cls.__mro__[1:]:
                if not universe.report_mismatch(f"{name} must explicitly inherit {registration.parent}"):
                    raise TypeError(f"{name} must explicitly inherit {registration.parent}")
        source = cls.__dict__.get("__v8_idl_spec__", IdlSpec())
        entries = universe.table.own_members(name)
        public_methods = {member.name for _, member in entries if member.kind == "operation"}
        public_attributes = {member.name for _, member in entries if member.kind == "attribute"}
        declared_methods = self._members(cls, parent, "methods", public_methods)
        declared_attributes = self._members(cls, parent, "attributes", public_attributes)
        methods = []
        attributes = []
        realm_methods = []
        seen = set()
        overrides = self.overrides
        for _location, member in entries:
            public_name = member.name
            kind = member.kind
            if not public_name or kind not in ("operation", "attribute") or (kind, public_name) in seen:
                continue
            seen.add((kind, public_name))
            common = member_flags(member)
            override = overrides.for_member(name, public_name) if overrides is not None else None
            if override is not None:
                logger.debug("interface override: {}.{} is answered by the host", name, public_name)
            if kind == "operation":
                declared = declared_methods.pop(public_name, None)
                static = member.is_static
                if override is not None and override.call is not None:
                    callback = override.call
                elif declared is not None:
                    raw = self.wrappers._raw_callback(cls, declared.callback)
                    callback = self.wrappers.method(cls, name, public_name, raw, static=static)
                    if static and self.wrappers._parameters_for(raw)[:1] == ("_realm",):
                        realm_methods.append((len(methods), raw))
                else:
                    callback = self.stubs.method(name, public_name, static=static)
                length = member.length if member.length is not None else (declared.length if declared else 0)
                methods.append(
                    MethodSpec(name=public_name, callback=callback, length=int(length),
                               cross_origin_check=cross_origin_check(member), **common)
                )
            else:
                declared = declared_attributes.pop(public_name, None)
                if override is not None and override.get is not None:
                    getter = override.get
                else:
                    getter = (
                        self.wrappers._raw_callback(cls, declared.getter)
                        if declared
                        else self.stubs.getter(name, public_name)
                    )
                setter = None
                if override is not None and override.set is not None:
                    # A hook that answers the write is what makes the member writable.
                    setter = override.set
                else:
                    writable = member.setter_state
                    if writable is None:
                        writable = declared is not None and declared.setter is not None
                    if writable:
                        setter = (
                            self.wrappers.setter(cls, name, public_name, declared.setter)
                            if declared is not None and declared.setter is not None
                            else self.stubs.setter(name, public_name)
                        )
                crossing = cross_origin_check(member)
                attributes.append(
                    AttributeSpec(name=public_name, getter=getter, setter=setter, replaceable=bool(declared and declared.replaceable), cross_origin_get_check=crossing, cross_origin_set_check=crossing, **common)
                )
        for public_name, declared in declared_methods.items():
            if universe.table.member(name, public_name, "operation") is None:
                if universe.table.declares(name, public_name, "operation"):
                    # The baseline has it, the browser does not install it.
                    continue
                if not universe.report_mismatch(
                        f"{name}.{public_name} is not present in the interface baseline"):
                    raise TypeError(f"{name}.{public_name} is not present in Chromium interface baseline")
                continue
            raw = self.wrappers._raw_callback(cls, declared.callback)
            static = declared.location == FlagLocation.kInterface
            callback = self.wrappers.method(cls, name, public_name, raw, static=static)
            if static and self.wrappers._parameters_for(raw)[:1] == ("_realm",):
                realm_methods.append((len(methods), raw))
            methods.append(declared.replace(callback=callback))
        for public_name, declared in declared_attributes.items():
            if universe.table.member(name, public_name, "attribute") is None:
                if universe.table.declares(name, public_name, "attribute"):
                    # Same as operations.
                    continue
                if not universe.report_mismatch(
                        f"{name}.{public_name} is not present in the interface baseline"):
                    raise TypeError(f"{name}.{public_name} is not present in Chromium interface baseline")
                continue
            getter = self.wrappers._raw_callback(cls, declared.getter)
            setter = None
            if declared.setter is not None:
                setter = self.wrappers.setter(cls, name, public_name, declared.setter)
            attributes.append(declared.replace(getter=getter, setter=setter))
        template = interface.template
        enabled = set()
        handlers = template.get("handlers") or {}
        cross = handlers.get("cross_origin") or {}
        for family, mapping in HANDLER_FAMILIES:
            enabled.update(callback for key, callback in mapping if key in (handlers.get(family) or {}))
        for family, mapping in CROSS_ORIGIN_FAMILIES:
            enabled.update(callback for key, callback in mapping if key in (cross.get(family) or {}))
        if template.get("access_check") or cross.get("named") or cross.get("indexed"):
            enabled.add("__v8_cross_origin_check__")
        handler_names = [
            callback for _, mapping in HANDLER_FAMILIES + CROSS_ORIGIN_FAMILIES for _, callback in mapping
        ] + ["__v8_cross_origin_check__"]
        options = {}
        declarations = tuple(
            base.__dict__["__v8_idl_spec__"] for base in cls.__mro__ if "__v8_idl_spec__" in base.__dict__
        )
        for callback_name in handler_names:
            key = callback_name[5:-2]
            reference = next(
                (getattr(declaration, key) for declaration in declarations if getattr(declaration, key) is not None),
                None,
            )
            if reference is not None:
                options[key] = self.wrappers._raw_callback(cls, reference)
            elif callback_name in enabled and self._fills_stub(callback_name, registration):
                options[key] = self.stubs.handler(name, callback_name)
        constants = {entry.name: entry for entry in source.constants_value}
        for field, value in cls.__dict__.items():
            if not field.startswith("_") and isinstance(value, int):
                constants.setdefault(field, ConstantSpec(name=field, value=value))
        for constant in interface.constants:
            if constant.get("resolved") and isinstance(constant.get("value"), int):
                constants[constant["name"]] = ConstantSpec(name=constant["name"], value=constant["value"])
        for _location, member in entries:
            if member.kind == "constant" and member.value is not None:
                constants[member.name] = ConstantSpec(name=member.name, value=member.value)
        options.update(
            methods=tuple(methods),
            attributes=tuple(attributes),
            constants_value=tuple(constants.values()),
            to_string_tag=interface.tag or source.to_string_tag or name,
            has_constructor=(
                source.has_constructor if "__v8_idl_spec__" in cls.__dict__
                else (1 if interface.has_interface_object else 0)
            ),
        )
        if parent_name is not None:
            # A parent that is not an interface has no template: the binding reads the Python base.
            options["parent_class"] = parent
        if source.call is not None:
            options["call"] = self.wrappers._raw_callback(cls, source.call)
        elif any("__call__" in base.__dict__ for base in cls.__mro__):
            options["call"] = self.wrappers._raw_callback(cls, "__call__")
        elif template.get("call_handler"):
            options["call"] = self.stubs.call_handler(name)
        apply_template_flags(options, template)
        if source.array:
            options["array"] = source.array
        if interface.constructor_length is not None:
            options["length"] = interface.constructor_length
        if interface.constructor_stated:
            options["constructor_behavior"] = (1 if interface.constructor_new else 0) | (
                2 if interface.constructor_call else 0
            )
        realm_constructor = False
        if options.get("constructor_behavior", source.constructor_behavior):
            options["constructor_adapter"] = self.wrappers.constructor_adapter(name, registration is None)
            realm_constructor = "_realm" in self.wrappers._parameters_for(cls)
        return CompiledInterface(source.replace(**options), tuple(realm_methods), realm_constructor)

    def bind(self, name, realm=None):
        compiled = self.compile(name)
        if realm is None or not (compiled.realm_methods or compiled.realm_constructor):
            return compiled.spec
        cls = self.universe.class_for(name)
        changes = {}
        if compiled.realm_methods:
            methods = list(compiled.spec.methods)
            for index, raw in compiled.realm_methods:
                item = methods[index]
                callback = self.wrappers.method(cls, name, item.name, raw, static=True, realm=realm)
                methods[index] = item.replace(callback=callback)
            changes["methods"] = tuple(methods)
        if compiled.realm_constructor:
            changes["constructor_adapter"] = self.wrappers.constructor_adapter(
                name, self.universe.runtime.interface(name) is None, realm
            )
        return compiled.spec.replace(**changes)
