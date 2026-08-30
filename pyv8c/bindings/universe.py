"""Per-Browser Chromium interface baseline and generated API shells."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import inspect
import time
import zipfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

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

from .webidl import BindingPlan, compile_binding, validate_arguments


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = PACKAGE_ROOT / "baselines" / "interface-baseline.zip"
DEFAULT_EAGER_INTERFACES = (
    "Window", "EventTarget", "Event", "Node", "Document", "Element",
    "HTMLElement", "URL", "URLSearchParams", "DOMTokenList", "NodeList",
    "HTMLCollection", "NamedNodeMap",
)
@dataclass(frozen=True)
class BaselineSnapshot:
    path: str
    digest: str
    metadata: Mapping[str, Any]
    interfaces: Mapping[str, Mapping[str, Any]]

    @property
    def info(self):
        metadata = dict(self.metadata)
        return {
            **metadata,
            "_webidl": metadata,
            "path": self.path,
            "sha256": self.digest,
            "interface_count": len(self.interfaces),
        }


class BaselineRepository:
    """Read immutable baseline snapshots, sharing only identical file contents."""

    _cache: dict[tuple[str, str], BaselineSnapshot] = {}
    _stat_cache: dict[tuple[str, int, int], BaselineSnapshot] = {}
    _last_timings: dict[str, float] = {}

    @staticmethod
    def load(path=None) -> BaselineSnapshot:
        started = time.perf_counter_ns()
        if path is None:
            target = DEFAULT_BASELINE_PATH
            label = f"pyv8c:{target.relative_to(PACKAGE_ROOT).as_posix()}"
        else:
            target = Path(path).expanduser().resolve()
            label = str(target)
        if not target.is_file():
            raise FileNotFoundError(f"Chromium interface baseline does not exist: {target}")
        if target.suffix.lower() not in (".zip", ".json"):
            raise ValueError(f"Chromium interface baseline must be a .zip or .json file: {target}")
        cache_target = target.with_suffix(".json") if target.suffix.lower() == ".zip" else target
        stat = target.stat()
        cache_stat = cache_target.stat() if cache_target.is_file() else None
        stat_key = (
            str(target.resolve()), stat.st_size, stat.st_mtime_ns,
            cache_stat.st_size if cache_stat else -1,
            cache_stat.st_mtime_ns if cache_stat else -1,
        )
        cached_by_stat = BaselineRepository._stat_cache.get(stat_key)
        if cached_by_stat is not None:
            BaselineRepository._last_timings = {
                "baseline_read_ms": 0.0,
                "baseline_decode_ms": 0.0,
                "baseline_compile_ms": 0.0,
                "baseline_cached": True,
            }
            return cached_by_stat
        try:
            if target.suffix.lower() == ".zip":
                if cache_stat is not None and cache_stat.st_mtime_ns >= stat.st_mtime_ns:
                    raw = cache_target.read_bytes()
                else:
                    with zipfile.ZipFile(target) as archive:
                        members = [
                            info for info in archive.infolist()
                            if not info.is_dir() and Path(info.filename).suffix.lower() == ".json"
                        ]
                        if len(members) != 1:
                            raise ValueError(f"baseline archive must contain exactly one JSON file: {target}")
                        member = members[0]
                        member_path = Path(member.filename)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise ValueError(f"baseline archive contains an unsafe member path: {member.filename}")
                        raw = archive.read(member)
                    try:
                        cache_target.write_bytes(raw)
                    except OSError as exc:
                        raise OSError(f"cannot write baseline cache next to archive: {cache_target}") from exc
            else:
                raw = target.read_bytes()
            decoded = time.perf_counter_ns()
            document = json.loads(raw.decode("utf-8"))
            parsed = time.perf_counter_ns()
        except UnicodeDecodeError as exc:
            raise ValueError(f"Chromium interface baseline must be UTF-8 JSON: {target}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Chromium interface baseline JSON: {target}") from exc
        if not isinstance(document, dict):
            raise ValueError("Chromium interface baseline root must be an object")
        metadata = document.get("_webidl")
        schema = metadata.get("schema") if isinstance(metadata, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("Chromium interface baseline must contain _webidl metadata")
        if schema != 5:
            raise ValueError(f"unsupported Chromium interface baseline schema {schema!r}; expected 5")
        compact = metadata.get("compact")
        if compact is not True:
            raise ValueError(
                "Chromium interface baseline is not a compact schema 5 runtime baseline; "
                "regenerate without --audit"
            )
        interfaces = {name: value for name, value in document.items() if not name.startswith("_")}
        if not interfaces:
            raise ValueError("Chromium interface baseline must contain interfaces")
        for name, spec in interfaces.items():
            if not isinstance(spec, dict):
                raise ValueError(f"invalid interface entry {name!r} in Chromium interface baseline")
            parent = spec.get("parent")
            if parent is not None and (not isinstance(parent, str) or parent not in interfaces):
                raise ValueError(f"interface {name!r} refers to unknown parent {parent!r}")
        visiting, visited = set(), set()

        def visit(name):
            if name in visiting:
                raise ValueError(f"interface parent cycle detected at {name!r}")
            if name in visited:
                return
            visiting.add(name)
            parent = interfaces[name].get("parent")
            if parent is not None:
                visit(parent)
            visiting.remove(name)
            visited.add(name)

        for name in interfaces:
            visit(name)
        digest = sha256(raw).hexdigest()
        key = (str(target.resolve()), digest)
        snapshot = BaselineRepository._cache.get(key)
        cache_hit = snapshot is not None
        if snapshot is None:
            # The decoded document is repository-owned and never mutated by
            # runtime code. A top-level read-only view avoids recursively
            # copying the 7.5 MB baseline during every cold process startup.
            snapshot = BaselineSnapshot(
                label, digest, MappingProxyType(metadata), MappingProxyType(interfaces),
            )
            BaselineRepository._cache[key] = snapshot
        BaselineRepository._stat_cache[stat_key] = snapshot
        frozen = time.perf_counter_ns()
        BaselineRepository._last_timings = {
            "baseline_read_ms": (decoded - started) / 1_000_000,
            "baseline_decode_ms": (parsed - decoded) / 1_000_000,
            "baseline_compile_ms": (frozen - parsed) / 1_000_000,
            "baseline_cached": cache_hit,
        }
        return snapshot


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

    def __init__(self, baseline: BaselineSnapshot, *, stub_logging="info",
                 expose_unimplemented=True, eager_interfaces=DEFAULT_EAGER_INTERFACES,
                 strict_validation=False, lazy_interfaces=True):
        self.baseline = baseline
        self.stub_logging = str(stub_logging or "info").lower()
        self.expose_unimplemented = bool(expose_unimplemented)
        self.eager_interfaces = tuple(dict.fromkeys(eager_interfaces or ()))
        self.strict_validation = bool(strict_validation)
        self.lazy_interfaces = bool(lazy_interfaces)
        self._classes: dict[str, type] = {}
        self._configs: dict[type, dict[str, Any]] = {}
        self._plans: dict[tuple[str, str, str], BindingPlan] = {}
        self._parent_chains = {}
        self._exposure_names = {}
        self._parameter_names: dict[object, tuple[str, ...]] = {}

    @property
    def info(self):
        return self.baseline.info

    def _log(self, message, *args):
        if self.stub_logging not in ("off", "none", "disabled"):
            logger.info(message, *args)

    def _spec(self, name):
        try:
            return self.baseline.interfaces[name]
        except KeyError as exc:
            raise TypeError(f"interface {name!r} is missing from Chromium interface baseline") from exc

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
        for candidate_name, candidate_spec in self.baseline.interfaces.items():
            cursor, descendants = candidate_name, set()
            while cursor and cursor not in descendants:
                descendants.add(cursor)
                if cursor == interface_name:
                    for location, member in self._own_members(candidate_name):
                        if member.get("name") == public_name and member.get("kind") == kind:
                            return candidate_name, location, member
                    break
                cursor = candidate_spec.get("parent") if cursor == candidate_name else self._spec(cursor).get("parent")
        return None

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
        from .registry import IMPLEMENTATIONS
        if name in self._classes:
            return self._classes[name]
        spec = self._spec(name)
        parent_name = spec.get("parent")
        parent = self.class_for(parent_name) if parent_name else object
        implementation = IMPLEMENTATIONS.get(name)
        if implementation is None:
            cls = type(name, (parent,), {
                "__module__": "pyv8c.bindings.generated",
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
    def _raw_callback(cls, name):
        for base in cls.__mro__:
            if name in base.__dict__:
                raw = base.__dict__[name]
                return raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
        raise TypeError(f"callback {cls.__name__}.{name} is missing")

    def _parameters_for(self, callback):
        names = self._parameter_names.get(callback)
        if names is None:
            names = tuple(inspect.signature(callback).parameters)
            self._parameter_names[callback] = names
        return names

    def _reject_conversion(self, receiver, exc):
        page = getattr(receiver, "_page", None)
        if page is None:
            raise exc
        promise, _resolve, reject = page._make_promise()
        page.post_task(lambda: reject(page._js_error_value(exc)))
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
                    wrapped_attributes.append((
                        member_name, getter.__name__, setter_name, CallbackType.kPy,
                        _location(location_name), _property_flags(member),
                        _receiver(member.get("receiver")), _cross_origin(member.get("cross_origin")),
                        _cross_origin(member.get("cross_origin")), _side_effect(member.get("side_effect")),
                    ))
                    configured_attributes.add(member_name)
        # Interceptor callbacks are discovered from the native template. Add
        # safe Python stubs so ClassConfig can install the corresponding V8
        # handlers even when no hand-written implementation exists.
        handler_methods = (
            "__v8_name_get__", "__v8_name_set__", "__v8_name_query__", "__v8_name_del__", "__v8_name_enum__",
            "__v8_index_get__", "__v8_index_set__", "__v8_index_query__", "__v8_index_del__", "__v8_index_enum__",
            "__v8_cross_origin_check__", "__v8_cross_origin_name_get__", "__v8_cross_origin_name_set__",
            "__v8_cross_origin_name_query__", "__v8_cross_origin_name_del__", "__v8_cross_origin_name_enum__",
            "__v8_cross_origin_index_get__", "__v8_cross_origin_index_set__", "__v8_cross_origin_index_query__",
            "__v8_cross_origin_index_del__", "__v8_cross_origin_index_enum__",
        )
        for method_name in handler_methods:
            if not any(method_name in base.__dict__ for base in cls.__mro__):
                callbacks[method_name] = self._stub_handler(name, method_name)
        callback_owner = type(f"_{name}Callbacks_{id(self):x}", (cls,), callbacks)
        return tuple(wrapped_methods), tuple(wrapped_attributes), callback_owner

    def class_config_for(self, name):
        return self._class_config_for(name, self._configs, None)

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
        template = spec.get("template") or {}
        handlers = template.get("handlers") or {}
        named = handlers.get("named") or {}
        indexed = handlers.get("indexed") or {}
        cross = handlers.get("cross_origin") or {}
        cross_named = cross.get("named") or {}
        cross_indexed = cross.get("indexed") or {}
        for key, method_name in (("get", "__v8_name_get__"), ("set", "__v8_name_set__"), ("query", "__v8_name_query__"), ("del", "__v8_name_del__"), ("enum", "__v8_name_enum__")):
            if key in named: config[method_name] = True
        for key, method_name in (("get", "__v8_index_get__"), ("set", "__v8_index_set__"), ("query", "__v8_index_query__"), ("del", "__v8_index_del__"), ("enum", "__v8_index_enum__")):
            if key in indexed: config[method_name] = True
        for key, method_name in (("get", "__v8_cross_origin_name_get__"), ("set", "__v8_cross_origin_name_set__"), ("query", "__v8_cross_origin_name_query__"), ("del", "__v8_cross_origin_name_del__"), ("enum", "__v8_cross_origin_name_enum__")):
            if key in cross_named: config[method_name] = True
        for key, method_name in (("get", "__v8_cross_origin_index_get__"), ("set", "__v8_cross_origin_index_set__"), ("query", "__v8_cross_origin_index_query__"), ("del", "__v8_cross_origin_index_del__"), ("enum", "__v8_cross_origin_index_enum__")):
            if key in cross_indexed: config[method_name] = True
        if template.get("access_check") or cross_named or cross_indexed:
            config["__v8_cross_origin_check__"] = True
        for source_key, config_key in (("immutable_proto", "__v8_immutable__"), ("undetectable", "__v8_undetectable__"), ("global_this", "__v8_global_this__"), ("as_object", "__v8_as_obj__"), ("unconstructable", "__v8_unconstructable__")):
            if template.get(source_key):
                config[config_key] = 1
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
        seen = set()
        while name and name not in seen:
            seen.add(name)
            yield name
            name = self._spec(name).get("parent")

    def exposed_names(self, context_type):
        from .registry import IMPLEMENTATIONS
        return [
            name for name, spec in self.baseline.interfaces.items()
            if context_type in (spec.get("exposed_in") or ())
            and (self.expose_unimplemented or name in IMPLEMENTATIONS)
        ]

    def class_configs(self, context_type, *, roots=()):
        return self._class_configs(context_type, roots=roots, cache=self._configs, realm=None)

    def _class_configs(self, context_type, *, roots=(), cache, realm):
        names = set(
            self.exposed_names(context_type)
            if not self.lazy_interfaces else self.eager_names(context_type)
        )
        names.update(root for root in roots if isinstance(root, str))
        for name in tuple(names):
            names.update(self._walk_parent_names(name))
        return {self.class_for(name): self._class_config_for(name, cache, realm) for name in names}

    def binding_session(self, realm=None):
        return BindingSession(self, realm)

    def validate(self):
        from .registry import IMPLEMENTATIONS, INTERFACES, validate_registry
        validate_registry(self.baseline.interfaces)
        for name, interface_spec in INTERFACES.items():
            cls = IMPLEMENTATIONS[name]
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

    def report(self, context_type="Window"):
        from .registry import IMPLEMENTATIONS
        report = {"implemented": [], "generated": [], "exposed": [], "skipped": []}
        for name, spec in self.baseline.interfaces.items():
            if context_type not in (spec.get("exposed_in") or ()):
                report["skipped"].append(name)
            else:
                report["exposed"].append(name)
                (report["implemented"] if name in IMPLEMENTATIONS else report["generated"]).append(name)
        return report


class BindingSession:
    """Context-local class configs and callback owners for one V8 realm."""

    def __init__(self, universe: InterfaceUniverse, realm=None):
        self.universe = universe
        self.realm = realm
        self._configs: dict[type, dict[str, Any]] = {}
        self._materialized: set[str] = set()

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

    def materialize_constructor(self, name):
        return self.materialize(name)

    def materialize_member(self, interface, member):
        self.materialize(interface)
        return self.universe._member(interface, member, "operation")

    def class_config_for(self, name):
        self.materialize(name)
        return self._configs[self.class_for(name)]

    def class_configs(self, context_type, *, roots=()):
        configs = self.universe._class_configs(
            context_type, roots=roots, cache=self._configs, realm=self.realm,
        )
        self._materialized.update(cls.__name__ for cls in configs)
        return configs
