"""Small Web IDL conversion and validation layer for JS-facing callbacks.

The native bridge already enforces prototype receivers and exposes Web IDL
``Function.length``.  This module handles the part that belongs at the Python
callback boundary: argument counts, conversions, optional values and common
interface/dictionary types.
"""

from dataclasses import dataclass, field
import functools
import inspect
import math
from typing import Any

import pyv8


MISSING = object()


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

    def required_count(self):
        if self.required is not None:
            return self.required
        return sum(1 for item in self.params if not item.optional and not item.variadic)


def param(type="any", *, optional=False, nullable=False, default=MISSING,
          variadic=False, enum=()):
    return ParamSpec(type, optional, nullable, default, variadic, tuple(enum))


def method(*params_, required=None, max_args=None, variadic=False, overloads=()):
    return MethodSpec(tuple(params_), required, max_args, variadic, tuple(overloads))


SIGNATURES: dict[tuple[str, str], MethodSpec] = {}
CONSTRUCTORS: dict[str, MethodSpec] = {}


def _register(interface, name, spec):
    SIGNATURES[(interface, name)] = spec


def _register_constructor(interface, spec):
    CONSTRUCTORS[interface] = spec


def signature_for(interface, name):
    """Resolve an operation signature through the explicit IDL parent chain."""
    current = interface
    while current:
        spec = SIGNATURES.get((current, name))
        if spec is not None:
            return spec
        try:
            from .interfaces import INTERFACES
            current = INTERFACES[current].parent
        except (KeyError, AttributeError):
            current = None
    return None


def _error(name, message):
    from .error import DOMException, V8RangeError, V8TypeError, V8URIError
    if name == "RangeError":
        return V8RangeError(message)
    if name == "URIError":
        return V8URIError(message)
    if name == "DOMException":
        return DOMException(message=message, name="TypeError")
    return V8TypeError(message)


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
    from .error import V8RangeError
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
    from .interfaces import IMPLEMENTATIONS
    expected = IMPLEMENTATIONS.get(name)
    if expected is None or not isinstance(value, expected):
        raise _error("TypeError", f"The provided value is not of type '{name}'.")
    return value


def _dictionary(value, nullable=False):
    if _is_null(value):
        if nullable:
            return value
        raise _error("TypeError", "The provided value is not an object.")
    if isinstance(value, dict):
        return value
    if isinstance(value, pyv8.JSObject):
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


def _convert(value, type_spec, *, nullable=False, enum=()):
    if type_spec in (None, "any", "object"):
        if _is_null(value) and not nullable and type_spec == "object":
            raise _error("TypeError", "The provided value is not an object.")
        return value
    if isinstance(type_spec, tuple) or isinstance(type_spec, list):
        # Overload/union resolution follows the JavaScript value shape.  In
        # particular, an options object must select the dictionary branch of
        # ``boolean | dictionary`` instead of Web-IDL boolean coercion (which
        # would turn every object into true).
        branches = list(type_spec)
        if isinstance(value, (dict, pyv8.JSObject)):
            branches.sort(key=lambda branch: 0 if (isinstance(branch, str) and (branch.startswith("dictionary") or branch.startswith("record<"))) else 1)
        elif isinstance(value, (list, tuple)):
            branches.sort(key=lambda branch: 0 if (isinstance(branch, str) and branch.startswith("sequence<")) else 1)
        failures = []
        for branch in branches:
            try:
                return _convert(value, branch, nullable=nullable, enum=enum)
            except Exception as exc:
                failures.append(exc)
        raise _error("TypeError", "The provided value does not match any permitted type.")
    if type_spec in ("DOMString", "USVString", "ByteString", "string"):
        result = _string(value)
        if type_spec == "ByteString" and any(ord(ch) > 255 for ch in result):
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
    if isinstance(type_spec, str) and type_spec.startswith("record<"):
        return _dictionary(value, nullable=nullable)
    if isinstance(type_spec, str) and type_spec.startswith("dictionary"):
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


def validate_arguments(interface, name, args, spec=None):
    spec = spec or SIGNATURES.get((interface, name)) or MethodSpec(required=0)
    if spec.overloads:
        errors = []
        for overload in spec.overloads:
            try:
                return validate_arguments(interface, name, args, overload)
            except Exception as exc:
                errors.append(exc)
        raise _error("TypeError", "Failed to resolve the operation overload.")

    required = spec.required_count()
    if len(args) < required:
        raise _error("TypeError", f"Failed to execute '{name}': {required} argument(s) required, but only {len(args)} present.")
    if spec.max_args is not None and len(args) > spec.max_args:
        raise _error("TypeError", f"Failed to execute '{name}': too many arguments.")

    converted = []
    fixed = spec.params
    for index, value in enumerate(args):
        if index < len(fixed):
            item = fixed[index]
            if _is_undefined(value) and item.optional:
                if item.default is not MISSING:
                    value = item.default
                converted.append(value)
            else:
                converted.append(_convert(value, item.type, nullable=item.nullable, enum=item.enum))
        elif spec.variadic or (fixed and fixed[-1].variadic):
            item = fixed[-1]
            converted.append(_convert(value, item.type, nullable=item.nullable, enum=item.enum))
        else:
            converted.append(value)
    return tuple(converted)


def validate_constructor(interface, args, spec=None):
    return validate_arguments(interface, "constructor", args, spec or CONSTRUCTORS.get(interface) or MethodSpec(required=0))


def validate_attribute(interface, name, value, spec=None):
    if spec is None:
        spec = SIGNATURES.get((interface, name))
    if spec is None or not spec.params:
        return value
    return validate_arguments(interface, name, (value,), spec)[0]


def _wrap_method(cls, interface, public_name, callback_name, spec, static=False):
    raw = cls.__dict__.get(callback_name)
    if raw is None:
        return
    function = raw.__func__ if isinstance(raw, staticmethod) else raw
    if getattr(function, "__webidl_wrapped__", False):
        return

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if kwargs:
            return function(*args, **kwargs)
        if static:
            converted = validate_arguments(interface, public_name, args, spec)
            return function(*converted)
        if not args:
            raise _error("TypeError", "Illegal invocation")
        try:
            converted = validate_arguments(interface, public_name, args[1:], spec)
        except Exception as exc:
            if interface == "Window" and public_name == "fetch" and args:
                # fetch() reports argument/conversion failures through its
                # Promise, unlike synchronous DOM operations.
                page = getattr(args[0], "_page", None)
                if page is not None:
                    promise, _resolve, reject = page._make_promise()
                    page.post_task(lambda exc=exc: reject(page._js_error_value(exc)))
                    return promise
            # Keep the established DOM diagnostic for appendChild while still
            # exposing a standard JavaScript TypeError object.
            if interface == "Node" and public_name == "appendChild":
                from .error import V8TypeError
                raise V8TypeError("The new child is not a Node") from exc
            raise
        return function(args[0], *converted)

    wrapper.__webidl_wrapped__ = True
    setattr(cls, callback_name, staticmethod(wrapper) if static else wrapper)


def _wrap_constructor(cls, interface, spec):
    raw = cls.__dict__.get("__init__")
    if raw is None or getattr(raw, "__webidl_wrapped__", False):
        return

    @functools.wraps(raw)
    def constructor(self, *args, **kwargs):
        # Internal DOM construction uses named page/node/runtime arguments;
        # JS constructors arrive as positional Web IDL arguments.
        if kwargs:
            return raw(self, *args, **kwargs)
        converted = validate_constructor(interface, args, spec)
        return raw(self, *converted)

    constructor.__webidl_wrapped__ = True
    setattr(cls, "__init__", constructor)


def install_interface_validators(cls, interface):
    from .interfaces import load_baseline
    baseline = load_baseline().get(interface, {})
    members = baseline.get("members", {})
    method_configs = getattr(cls, "__v8_method__", ()) or ()
    for config in method_configs:
        public_name, callback_name, _length = config[0], config[1], config[2]
        spec = signature_for(interface, public_name) or MethodSpec(required=max(0, _length))
        static = config[4] == 2
        _wrap_method(cls, interface, public_name, callback_name, spec, static=static)
    for config in getattr(cls, "__v8_attribute__", ()) or ():
        public_name, _getter, setter = config[0], config[1], config[2]
        if setter:
            spec = SIGNATURES.get((interface, public_name))
            if spec and hasattr(cls, setter):
                _wrap_method(cls, interface, public_name, setter, spec, static=False)
    constructor = CONSTRUCTORS.get(interface)
    if constructor is not None:
        _wrap_constructor(cls, interface, constructor)


def _install_signatures():
    string = param("DOMString")
    optional_string = param("DOMString", optional=True)
    options = param("dictionary", optional=True, nullable=True, default=None)
    event = param("Event")
    node = param("Node")

    for name in ("Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "MessageEvent", "CloseEvent", "ErrorEvent"):
        _register_constructor(name, method(string, options, required=1))
    _register_constructor("EventTarget", method(required=0))
    _register_constructor("DOMException", method(param("DOMString", optional=True), param("DOMString", optional=True), required=0))
    _register_constructor("DOMError", method(param("DOMString", optional=True), param("DOMString", optional=True), required=0))
    for name in ("Text", "Comment"):
        _register_constructor(name, method(param("DOMString", optional=True), required=0))
    _register_constructor("DocumentFragment", method(required=0))
    _register_constructor("URL", method(string, optional_string, required=1))
    _register_constructor("URLSearchParams", method(param("any", optional=True), required=0))
    _register_constructor("Blob", method(param("sequence<any>", optional=True), options, required=0))
    # The Web IDL optional init values are nullable in the implementation: an
    # omitted/undefined/null init all mean an empty Headers object.
    _register_constructor("Headers", method(param(("sequence<any>", "dictionary", "Headers"), optional=True, nullable=True), required=0))
    _register_constructor("Request", method(param(("DOMString", "Request")), options, required=1))
    _register_constructor("Response", method(param("any", optional=True), options, required=0))
    _register_constructor("WebSocket", method(string, param(("DOMString", "sequence<DOMString>"), optional=True), required=1))
    _register_constructor("Worker", method(string, required=1))
    _register_constructor("XMLHttpRequest", method(required=0))

    _register("EventTarget", "addEventListener", method(string, param("callback", nullable=True), param(("boolean", "dictionary"), optional=True), required=2))
    _register("EventTarget", "removeEventListener", method(string, param("callback", nullable=True), param(("boolean", "dictionary"), optional=True), required=2))
    _register("EventTarget", "dispatchEvent", method(event, required=1))
    for name in ("preventDefault", "stopPropagation", "stopImmediatePropagation", "composedPath"):
        _register("Event", name, method(required=0))

    _register("Node", "appendChild", method(node, required=1))
    _register("Node", "insertBefore", method(node, param("Node", nullable=True), required=2))
    _register("Node", "removeChild", method(node, required=1))
    _register("Node", "replaceChild", method(node, node, required=2))
    _register("Node", "cloneNode", method(param("boolean", optional=True, default=False), required=0))
    _register("Node", "contains", method(param("Node", nullable=True), required=1))
    _register("Node", "isEqualNode", method(param("Node", nullable=True), required=1))
    _register("Node", "hasChildNodes", method(required=0))
    _register("Node", "textContent", method(param("DOMString", nullable=True), required=1))
    _register("Node", "nodeValue", method(param("DOMString", nullable=True), required=1))
    for name in ("item", "key"):
        _register("NodeList", name, method(param("unsigned long"), required=1))
    _register("HTMLCollection", "item", method(param("unsigned long"), required=1))
    _register("HTMLCollection", "namedItem", method(string, required=1))
    _register("NamedNodeMap", "item", method(param("unsigned long"), required=1))
    _register("NamedNodeMap", "getNamedItem", method(string, required=1))
    _register("NamedNodeMap", "setNamedItem", method(param("Attr"), required=1))
    _register("NamedNodeMap", "removeNamedItem", method(string, required=1))
    _register("DOMTokenList", "item", method(param("unsigned long"), required=1))
    _register("DOMTokenList", "contains", method(string, required=1))
    _register("DOMTokenList", "add", method(param("DOMString", variadic=True), required=1, variadic=True))
    _register("DOMTokenList", "remove", method(param("DOMString", variadic=True), required=1, variadic=True))
    _register("DOMTokenList", "toggle", method(string, param("boolean", optional=True), required=1))
    _register("DOMTokenList", "replace", method(string, string, required=2))

    for name in ("getAttribute", "removeAttribute", "hasAttribute", "querySelector", "querySelectorAll", "matches", "closest"):
        _register("Element", name, method(string, required=1))
    _register("Element", "setAttribute", method(string, string, required=2))
    for name in ("createElement", "createAttribute", "getElementById", "getElementsByTagName", "getElementsByClassName", "querySelector", "querySelectorAll"):
        _register("Document", name, method(string, required=1))
    for name in ("createTextNode", "createComment"):
        _register("Document", name, method(string, required=1))
    _register("Document", "createDocumentFragment", method(required=0))

    for name in ("getItem", "removeItem", "key"):
        _register("Storage", name, method(param("long") if name == "key" else string, required=1))
    _register("Headers", "has", method(string, required=1))
    _register("Storage", "setItem", method(string, string, required=2))
    _register("Storage", "clear", method(required=0))
    _register("Headers", "get", method(string, required=1))
    _register("Headers", "set", method(string, string, required=2))
    _register("Headers", "append", method(string, string, required=2))
    _register("Headers", "delete", method(string, required=1))
    _register("Blob", "slice", method(param("long", optional=True, default=0), param("long", optional=True), optional_string, required=0))
    for name in ("text", "arrayBuffer"):
        _register("Blob", name, method(required=0))
    for name in ("text", "json", "arrayBuffer"):
        _register("Response", name, method(required=0))
    _register("URLSearchParams", "append", method(string, string, required=2))
    _register("URLSearchParams", "delete", method(string, required=1))
    _register("URLSearchParams", "get", method(string, required=1))
    _register("URLSearchParams", "getAll", method(string, required=1))
    _register("URLSearchParams", "has", method(string, required=1))
    _register("URLSearchParams", "set", method(string, string, required=2))
    _register("URLSearchParams", "sort", method(required=0))
    _register("URLSearchParams", "toString", method(required=0))
    _register("URL", "canParse", method(string, optional_string, required=1))
    _register("URL", "parse", method(string, optional_string, required=1))
    _register("URL", "createObjectURL", method(param("Blob"), required=1))
    _register("URL", "revokeObjectURL", method(string, required=1))
    for name in ("toString", "toJSON"):
        _register("URL", name, method(required=0))

    _register("History", "pushState", method(param("any"), string, optional_string, required=2))
    _register("History", "replaceState", method(param("any"), string, optional_string, required=2))
    _register("History", "go", method(param("long", optional=True, default=0), required=0))
    for name in ("back", "forward"):
        _register("History", name, method(required=0))
    _register("Window", "setTimeout", method(param("callback"), param("long", optional=True, default=0), required=1))
    _register("Window", "setInterval", method(param("callback"), param("long", optional=True, default=0), required=1))
    for name in ("clearTimeout", "clearInterval"):
        _register("Window", name, method(param("long"), required=1))
    _register("Window", "requestAnimationFrame", method(param("callback"), required=1))
    _register("Window", "queueMicrotask", method(param("callback"), required=1))
    _register("Window", "fetch", method(param(("DOMString", "Request")), param("dictionary", optional=True, nullable=True), required=1))
    _register("XMLHttpRequest", "open", method(string, string, param("boolean", optional=True, default=True), optional_string, optional_string, required=2))
    _register("XMLHttpRequest", "send", method(param("any", optional=True), required=0))
    _register("XMLHttpRequest", "setRequestHeader", method(string, string, required=2))
    _register("XMLHttpRequest", "getResponseHeader", method(string, required=1))
    _register("XMLHttpRequest", "abort", method(required=0))
    _register("WebSocket", "send", method(param("any"), required=1))
    _register("WebSocket", "close", method(param("unsigned short", optional=True, default=1000), optional_string, required=0))
    _register("Worker", "postMessage", method(param("any"), required=1))
    _register("Worker", "terminate", method(required=0))
    _register("WorkerGlobalScope", "postMessage", method(param("any"), required=1))
    _register("WorkerGlobalScope", "close", method(required=0))

    # Attribute setters are callbacks too.  Keep their conversion separate
    # from the getter metadata so null/undefined and numeric coercion follow
    # Web IDL rather than Python's implicit str()/int().
    for interface_name, names in {
        "Location": ("href", "search", "hash", "pathname"),
        "URL": ("href", "protocol", "username", "password", "host", "hostname", "port", "pathname", "search", "hash"),
        "Element": ("id", "className", "innerHTML", "outerHTML"),
        "HTMLElement": ("title",),
        "HTMLImageElement": ("src", "alt"),
        "HTMLScriptElement": ("src", "type"),
        "HTMLLinkElement": ("href", "rel"),
        "HTMLStyleElement": ("media",),
        "HTMLIFrameElement": ("src",),
    }.items():
        for name in names:
            _register(interface_name, name, method(string, required=1))
    _register("Window", "location", method(param(("DOMString", "Location")), required=1))
    _register("DOMTokenList", "value", method(string, required=1))
    _register("Attr", "value", method(string, required=1))
    _register("Event", "cancelBubble", method(param("boolean"), required=1))
    _register("Event", "returnValue", method(param("boolean"), required=1))
    for name in ("onload", "onerror", "onreadystatechange", "onpageshow", "onpagehide", "onunload", "onbeforeunload"):
        _register("EventTarget", name, method(param("callback", nullable=True), required=1))
    for name in ("onopen", "onmessage", "onerror", "onclose"):
        _register("WebSocket", name, method(param("callback", nullable=True), required=1))
    for name in ("onmessage", "onerror"):
        _register("WorkerGlobalScope", name, method(param("callback", nullable=True), required=1))
    _register("WebSocket", "binaryType", method(param("enum:blob|arraybuffer"), required=1))


_install_signatures()
