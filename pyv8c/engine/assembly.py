"""Compile realm-independent IDL structure once, bind only realm-dependent callbacks."""

from dataclasses import dataclass

from pyv8 import AttributeSpec, ConstantSpec, IdlSpec, MethodSpec
from pyv8.flag import (
    CallbackType,
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
    result = V8Attribute.ReadOnly if "ReadOnly" in flags or "DontWrite" in flags else 0
    if "DontEnum" in flags:
        result |= V8Attribute.DontEnum
    if "DontDelete" in flags:
        result |= V8Attribute.DontDelete
    return result


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


@dataclass(frozen=True)
class CompiledInterface:
    spec: IdlSpec
    realm_methods: tuple
    realm_constructor: bool


class InterfaceCompiler:
    def __init__(self, universe, stubs, wrappers, lock):
        self.universe = universe
        self.stubs = stubs
        self.wrappers = wrappers
        self._compiled = {}
        self._lock = lock

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
        baseline = universe.spec(name)
        parent_name = baseline.get("parent")
        parent = universe.class_for(parent_name) if parent_name else object
        implementation = universe.runtime.interface(name)
        registration = getattr(implementation, "__idl_spec__", None)
        if registration is not None:
            if registration.parent and registration.parent not in universe.runtime.catalog.interfaces:
                raise TypeError(f"{name} refers to unknown parent {registration.parent}")
            if parent_name is not None and registration.parent != parent_name:
                raise TypeError(f"{name} parent does not match baseline")
            if registration.parent and universe.class_for(registration.parent) not in cls.__mro__[1:]:
                raise TypeError(f"{name} must explicitly inherit {registration.parent}")
        source = cls.__dict__.get("__v8_idl_spec__", IdlSpec())
        entries = list(universe.own_members(name))
        public_methods = {member.get("name") for _, member in entries if member.get("kind") == "operation"}
        public_attributes = {member.get("name") for _, member in entries if member.get("kind") == "attribute"}
        declared_methods = self._members(cls, parent, "methods", public_methods)
        declared_attributes = self._members(cls, parent, "attributes", public_attributes)
        methods = []
        attributes = []
        realm_methods = []
        seen = set()
        for location, member in entries:
            public_name = member.get("name")
            kind = member.get("kind")
            if not public_name or kind not in ("operation", "attribute") or (kind, public_name) in seen:
                continue
            seen.add((kind, public_name))
            common = dict(
                location=_location(location),
                property_flags=_property_flags(member),
                receiver_check=_receiver(member.get("receiver")),
                side_effect=_side_effect(member.get("side_effect")),
            )
            if kind == "operation":
                declared = declared_methods.pop(public_name, None)
                if declared is not None and declared.callback_type == CallbackType.kJs:
                    methods.append(
                        declared.replace(
                            length=member.get("length", declared.length),
                            cross_origin_check=_cross_origin(member.get("cross_origin")),
                            **common,
                        )
                    )
                    continue
                static = location == "interface"
                if declared is not None:
                    raw = self.wrappers._raw_callback(cls, declared.callback)
                    callback = self.wrappers.method(cls, name, public_name, raw, static=static)
                    if static and self.wrappers._parameters_for(raw)[:1] == ("_realm",):
                        realm_methods.append((len(methods), raw))
                else:
                    callback = self.stubs.method(name, public_name, static=static)
                methods.append(
                    MethodSpec(
                        name=public_name,
                        callback=callback,
                        length=int(
                            member["length"] if member.get("length") is not None else declared.length if declared else 0
                        ),
                        cross_origin_check=_cross_origin(member.get("cross_origin")),
                        **common,
                    )
                )
            else:
                declared = declared_attributes.pop(public_name, None)
                if declared is not None and declared.callback_type == CallbackType.kJs:
                    attributes.append(
                        declared.replace(
                            setter=None if "setter" in member and member["setter"] is None else declared.setter,
                            cross_origin_get_check=_cross_origin(member.get("cross_origin")),
                            cross_origin_set_check=_cross_origin(member.get("cross_origin")),
                            **common,
                        )
                    )
                    continue
                getter = (
                    self.wrappers._raw_callback(cls, declared.getter)
                    if declared
                    else self.stubs.getter(name, public_name)
                )
                setter = None
                writable = bool(member.get("setter") or member.get("setter_callback") or member.get("has_setter"))
                if "setter" not in member and declared is not None:
                    writable = declared.setter is not None
                if writable:
                    setter = (
                        self.wrappers.setter(cls, name, public_name, declared.setter)
                        if declared is not None and declared.setter is not None
                        else self.stubs.setter(name, public_name)
                    )
                attributes.append(
                    AttributeSpec(
                        name=public_name,
                        getter=getter,
                        setter=setter,
                        replaceable=bool(declared and declared.replaceable),
                        cross_origin_get_check=_cross_origin(member.get("cross_origin")),
                        cross_origin_set_check=_cross_origin(member.get("cross_origin")),
                        **common,
                    )
                )
        for public_name, declared in declared_methods.items():
            if universe._member(name, public_name, "operation") is None:
                raise TypeError(f"{name}.{public_name} is not present in Chromium interface baseline")
            if declared.callback_type == CallbackType.kJs:
                methods.append(declared)
                continue
            raw = self.wrappers._raw_callback(cls, declared.callback)
            static = declared.location == FlagLocation.kInterface
            callback = self.wrappers.method(cls, name, public_name, raw, static=static)
            if static and self.wrappers._parameters_for(raw)[:1] == ("_realm",):
                realm_methods.append((len(methods), raw))
            methods.append(declared.replace(callback=callback))
        for public_name, declared in declared_attributes.items():
            if declared.setter is not None and universe._member(name, public_name, "attribute") is None:
                raise TypeError(f"{name}.{public_name} is not present in Chromium interface baseline")
            if declared.callback_type == CallbackType.kJs:
                attributes.append(declared)
                continue
            getter = self.wrappers._raw_callback(cls, declared.getter)
            setter = None
            if declared.setter is not None:
                setter = self.wrappers.setter(cls, name, public_name, declared.setter)
            attributes.append(declared.replace(getter=getter, setter=setter))
        template = baseline.get("template") or {}
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
            elif registration is None and callback_name in enabled:
                options[key] = self.stubs.handler(name, callback_name)
        constants = {entry.name: entry for entry in source.constants_value}
        for field, value in cls.__dict__.items():
            if not field.startswith("_") and isinstance(value, int):
                constants.setdefault(field, ConstantSpec(name=field, value=value))
        for constant in baseline.get("constants") or ():
            if constant.get("resolved") and isinstance(constant.get("value"), int):
                constants[constant["name"]] = ConstantSpec(name=constant["name"], value=constant["value"])
        for _, member in entries:
            if member.get("kind") == "constant" and isinstance(member.get("value"), int):
                constants[member["name"]] = ConstantSpec(name=member["name"], value=member["value"])
        options.update(
            methods=tuple(methods),
            attributes=tuple(attributes),
            constants_value=tuple(constants.values()),
            parent_class=parent,
            to_string_tag=baseline.get("tag") or source.to_string_tag or name,
            has_constructor=source.has_constructor if "__v8_idl_spec__" in cls.__dict__ else 1,
        )
        if source.call is not None:
            options["call"] = self.wrappers._raw_callback(cls, source.call)
        elif any("__call__" in base.__dict__ for base in cls.__mro__):
            options["call"] = self.wrappers._raw_callback(cls, "__call__")
        elif template.get("call_handler"):
            options["call"] = self.stubs.call_handler(name)
        apply_template_flags(options, template)
        if source.array:
            options["array"] = source.array
        constructor = baseline.get("constructor") or {}
        if constructor.get("length") is not None:
            options["length"] = int(constructor["length"])
        if constructor.get("allow_new") is not None or constructor.get("allow_call") is not None:
            options["constructor_behavior"] = (1 if constructor.get("allow_new") else 0) | (
                2 if constructor.get("allow_call") else 0
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
