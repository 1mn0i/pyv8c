"""Generic Web IDL signature compilation and JavaScript value conversion.

Interface-specific signatures live in the selected Chromium baseline. This
module deliberately contains no interface registry: each InterfaceUniverse
compiles its own immutable binding plans from that baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
from typing import Any, Mapping

import pyv8


MISSING = object()
RESOLVED_STATUSES = frozenset(("generated", "manual-reviewed"))


@dataclass(frozen=True)
class ParamSpec:
    type: Any = "any"
    optional: bool = False
    nullable: bool = False
    default: Any = MISSING
    variadic: bool = False
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodSpec:
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    required: int | None = None
    max_args: int | None = None
    variadic: bool = False
    overloads: tuple["MethodSpec", ...] = ()
    # Computed once at compile time so argument validation never rescans
    # the parameter tuple.
    required_arg_count: int | None = None

    def __post_init__(self):
        if self.required_arg_count is None:
            if self.required is not None:
                count = self.required
            else:
                count = sum(
                    1 for item in self.params
                    if not item.optional and not item.variadic
                )
            object.__setattr__(self, "required_arg_count", count)

    def required_count(self):
        return self.required_arg_count


@dataclass(frozen=True)
class BindingPlan:
    interface: str
    name: str
    kind: str
    signature: MethodSpec
    status: str
    source_evidence: str = ""
    error_mode: str = "throw"
    # Pre-built plans for each overload; avoids rebuilding a BindingPlan on
    # every call that has to try more than one signature.
    overload_plans: tuple["BindingPlan", ...] = ()

    @property
    def resolved(self):
        return self.status in RESOLVED_STATUSES


def _error(name, message):
    from ..core.errors import DOMException, V8RangeError, V8TypeError, V8URIError
    if name == "RangeError":
        return V8RangeError(message)
    if name == "URIError":
        return V8URIError(message)
    if name == "DOMException":
        return DOMException(message=message, name="TypeError")
    return V8TypeError(message)


def _status(signature):
    value = signature.get("status") or signature.get("review")
    return {
        "generated-callback": "generated",
        "generated": "generated",
        "manual-reviewed": "manual-reviewed",
        "manual-review-required": "unresolved",
        "unresolved": "unresolved",
    }.get(value, "unresolved")


def _template_argument(value, wrapper):
    prefix = wrapper + "<"
    if isinstance(value, str) and value.startswith(prefix) and value.endswith(">"):
        return value[len(prefix):-1]
    return None


def _split_template_arguments(value):
    result, start, depth = [], 0, 0
    for index, character in enumerate(value):
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif character == "," and depth == 0:
            result.append(value[start:index].strip())
            start = index + 1
    result.append(value[start:].strip())
    return result


_BLINK_TYPES = {
    "IDLAny": "any",
    "IDLBoolean": "boolean",
    "IDLByteString": "ByteString",
    "IDLDouble": "double",
    "IDLFloat": "double",
    "IDLLong": "long",
    "IDLString": "DOMString",
    "IDLUnrestrictedDouble": "unrestricted double",
    "IDLUnsignedLong": "unsigned long",
    "IDLUnsignedShort": "unsigned short",
    "IDLUnsignedShortClamp": "unsigned short",
    "IDLUSVString": "USVString",
}


def normalize_type(type_spec):
    """Convert generated Blink trait names to the runtime's canonical form."""
    if isinstance(type_spec, (tuple, list)):
        return tuple(normalize_type(item) for item in type_spec)
    if isinstance(type_spec, Mapping):
        kind = type_spec.get("kind")
        if kind == "union":
            return tuple(normalize_type(item) for item in type_spec.get("members", ()))
        if kind == "sequence":
            return f"sequence<{normalize_type(type_spec.get('item', 'any'))}>"
        if kind == "record":
            return "record<{},{}>".format(
                normalize_type(type_spec.get("key", "DOMString")),
                normalize_type(type_spec.get("value", "any")),
            )
        if kind == "enum":
            return "enum:" + "|".join(type_spec.get("values", ()))
        if kind in ("dictionary", "callback", "interface"):
            return type_spec.get("name") or kind
        return kind or "any"
    if not isinstance(type_spec, str):
        return type_spec
    value = re.sub(r"\s+", "", type_spec)
    for wrapper in ("IDLOptional", "IDLNullable"):
        inner = _template_argument(value, wrapper)
        if inner is not None:
            return normalize_type(inner)
    inner = _template_argument(value, "IDLSequence")
    if inner is not None:
        return f"sequence<{normalize_type(inner)}>"
    inner = _template_argument(value, "IDLRecord")
    if inner is not None:
        parts = _split_template_arguments(inner)
        return "record<{}>".format(",".join(str(normalize_type(item)) for item in parts))
    return _BLINK_TYPES.get(value, type_spec)


def _default(value):
    if value == "false":
        return False
    if value == "true":
        return True
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def compile_signature(signature) -> MethodSpec:
    signature = signature or {}
    required = signature.get("required")
    parameters = []
    for index, raw in enumerate(signature.get("parameters") or ()):
        raw_type = raw.get("type", raw.get("idl_type", "any"))
        optional_wrapper = _template_argument(raw_type, "IDLOptional") if isinstance(raw_type, str) else None
        nullable_wrapper = _template_argument(raw_type, "IDLNullable") if isinstance(raw_type, str) else None
        parameters.append(ParamSpec(
            normalize_type(optional_wrapper or nullable_wrapper or raw_type),
            bool(raw.get("optional") or optional_wrapper is not None or (required is not None and index >= int(required))),
            bool(raw.get("nullable") or nullable_wrapper is not None),
            _default(raw.get("default")) if "default" in raw else MISSING,
            bool(raw.get("variadic")),
            tuple(raw.get("enum") or ()),
        ))
    return MethodSpec(
        tuple(parameters),
        int(required) if required is not None else None,
        int(signature["max_args"]) if signature.get("max_args") is not None else None,
        bool(signature.get("variadic")),
        tuple(compile_signature(item) for item in signature.get("overloads") or ()),
    )


def compile_binding(interface, name, kind, signature) -> BindingPlan:
    signature = signature or {}
    status = _status(signature)
    evidence = str(signature.get("source_evidence") or "")
    error_mode = str(signature.get("error_mode") or "throw")
    plan = BindingPlan(
        interface,
        name,
        kind,
        compile_signature(signature),
        status,
        evidence,
        error_mode,
    )
    if plan.signature.overloads:
        plan = replace(
            plan,
            overload_plans=tuple(
                BindingPlan(interface, name, kind, overload, status, evidence, error_mode)
                for overload in plan.signature.overloads
            ),
        )
    return plan


def _is_undefined(value):
    return value is pyv8.Undefined


def _is_null(value):
    return value is None or value is pyv8.Null


def _string(value):
    if value is pyv8.Undefined:
        return "undefined"
    if value is pyv8.Null or value is None:
        return "null"
    return str(value)


def _boolean(value):
    if _is_null(value) or _is_undefined(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return bool(value)
    return True


def _number(value, integer=False, unsigned=False):
    from ..core.errors import V8RangeError
    if _is_null(value) or _is_undefined(value) or isinstance(value, bool):
        raise _error("TypeError", "The provided value is not a number.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error("TypeError", "The provided value is not a number.") from exc
    if not math.isfinite(result):
        raise _error("RangeError", "The number is not finite.")
    if integer:
        result = math.trunc(result)
        low = 0 if unsigned else -(2 ** 31)
        high = (2 ** 32) - 1 if unsigned else (2 ** 31) - 1
        if result < low or result > high:
            raise V8RangeError("The number is outside the permitted range.")
        return int(result)
    return result


def _interface(value, name, nullable=False):
    if _is_null(value):
        if nullable:
            return value
        raise _error("TypeError", f"The provided value is not of type '{name}'.")
    from .registry import IMPLEMENTATIONS
    expected = IMPLEMENTATIONS.get(name)
    # The registered implementation is the common case, so test it first and
    # only walk the MRO when that fails.
    if expected is not None and isinstance(value, expected):
        return value
    if name in {getattr(cls, "__idl_name__", None) for cls in type(value).__mro__}:
        return value
    raise _error("TypeError", f"The provided value is not of type '{name}'.")


def _dictionary(value, nullable=False):
    if _is_null(value):
        if nullable:
            return value
        raise _error("TypeError", "The provided value is not an object.")
    if isinstance(value, (dict, pyv8.JSObject)):
        return value
    raise _error("TypeError", "The provided value is not an object.")


def _sequence(value, item_type="any", nullable=False):
    if _is_null(value):
        if nullable:
            return value
        raise _error("TypeError", "The provided value is not iterable.")
    if isinstance(value, dict):
        raise _error("TypeError", "The provided value is not iterable.")
    try:
        values = list(value)
    except (TypeError, ValueError) as exc:
        raise _error("TypeError", "The provided value is not iterable.") from exc
    return [_convert(item, item_type) for item in values]


def _long(value):
    return _number(value, integer=True)


def _unsigned_long(value):
    return _number(value, integer=True, unsigned=True)


def _byte_string(value):
    result = _string(value)
    if any(ord(character) > 255 for character in result):
        raise _error("TypeError", "The value is not a valid ByteString.")
    return result


# Single dict lookup for the scalar types that dominate real signatures.
# Types carrying extra constraints (enums, ByteString bounds) fall through to
# the explicit branches below so their checks are never skipped.
_SCALAR_CONVERTERS = {
    "DOMString": _string,
    "USVString": _string,
    "string": _string,
    "boolean": _boolean,
    "bool": _boolean,
    "double": _number,
    "unrestricted double": _number,
    "number": _number,
    "long": _long,
    "int": _long,
    "unsigned long": _unsigned_long,
    "unsigned short": _unsigned_long,
    "uint": _unsigned_long,
    "ByteString": _byte_string,
}


def _convert(value, type_spec, *, nullable=False, enum=()):
    if type_spec in (None, "any", "object"):
        if _is_null(value) and not nullable and type_spec == "object":
            raise _error("TypeError", "The provided value is not an object.")
        return value
    if not enum and isinstance(type_spec, str):
        converter = _SCALAR_CONVERTERS.get(type_spec)
        if converter is not None:
            return converter(value)
    if isinstance(type_spec, (tuple, list)):
        # Try the branches matching the runtime shape first. Filtering in
        # place avoids allocating and sorting a list on every union call.
        if isinstance(value, (dict, pyv8.JSObject)):
            preferred = ("dictionary", "record<")
        elif isinstance(value, (list, tuple)):
            preferred = ("sequence<",)
        else:
            preferred = ()
        for branch in type_spec:
            if preferred and isinstance(branch, str) and branch.startswith(preferred):
                try:
                    return _convert(value, branch, nullable=nullable, enum=enum)
                except Exception:
                    pass
        for branch in type_spec:
            if preferred and isinstance(branch, str) and branch.startswith(preferred):
                continue
            try:
                return _convert(value, branch, nullable=nullable, enum=enum)
            except Exception:
                pass
        raise _error("TypeError", "The provided value does not match any permitted type.")
    if type_spec in ("DOMString", "USVString", "ByteString", "string"):
        result = _string(value)
        if type_spec == "ByteString" and any(ord(character) > 255 for character in result):
            raise _error("TypeError", "The value is not a valid ByteString.")
        if enum and result not in enum:
            raise _error("TypeError", "The provided value is not a valid enum value.")
        return result
    if type_spec in ("boolean", "bool"):
        return _boolean(value)
    if type_spec in ("double", "unrestricted double", "number"):
        return _number(value)
    if type_spec in ("long", "int"):
        return _number(value, integer=True)
    if type_spec in ("unsigned long", "unsigned short", "uint"):
        return _number(value, integer=True, unsigned=True)
    if type_spec in ("sequence", "sequence<any>"):
        return _sequence(value)
    if isinstance(type_spec, str) and type_spec.startswith("sequence<"):
        return _sequence(value, type_spec[9:-1])
    if isinstance(type_spec, str) and (type_spec.startswith("record<") or type_spec.startswith("dictionary")):
        return _dictionary(value, nullable=nullable)
    if type_spec in ("callback", "Function"):
        if _is_null(value) and nullable:
            return value
        if not callable(value):
            raise _error("TypeError", "The provided value is not a function.")
        return value
    if isinstance(type_spec, str) and type_spec.startswith("enum:"):
        allowed = tuple(type_spec[5:].split("|"))
        result = _string(value)
        if result not in allowed:
            raise _error("TypeError", "The provided value is not a valid enum value.")
        return result
    return _interface(value, type_spec, nullable=nullable)


def validate_arguments(plan: BindingPlan, args):
    from ..core.errors import V8TypeError

    spec = plan.signature
    if spec.overloads:
        # Overload plans are built once by compile_binding.
        for nested in plan.overload_plans or tuple(
            BindingPlan(plan.interface, plan.name, plan.kind, overload, plan.status,
                        plan.source_evidence, plan.error_mode)
            for overload in spec.overloads
        ):
            try:
                return validate_arguments(nested, args)
            except Exception:
                pass
        raise _error("TypeError", f"Failed to execute '{plan.name}': no matching overload.")
    required = spec.required_count()
    if len(args) < required:
        raise _error(
            "TypeError",
            f"Failed to execute '{plan.name}': {required} argument(s) required, but only {len(args)} present.",
        )
    if spec.max_args is not None and len(args) > spec.max_args:
        raise _error("TypeError", f"Failed to execute '{plan.name}': too many arguments.")
    converted = []
    for index, item in enumerate(spec.params):
        if index >= len(args):
            break
        value = args[index]
        if _is_undefined(value) and item.optional:
            if item.default is MISSING:
                converted.append(value)
                continue
            value = item.default
        try:
            converted.append(_convert(value, item.type, nullable=item.nullable, enum=item.enum))
        except V8TypeError as exc:
            if "not of type" not in str(exc):
                raise
            raise V8TypeError(
                f"Failed to execute '{plan.name}' on '{plan.interface}': "
                f"parameter {index + 1} is not of type '{item.type}'."
            ) from exc
    if spec.variadic or (spec.params and spec.params[-1].variadic):
        item = spec.params[-1]
        for value in args[len(spec.params):]:
            converted.append(_convert(value, item.type, nullable=item.nullable, enum=item.enum))
    return tuple(converted)
