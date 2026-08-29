"""Extract a reviewable interface snapshot from generated Blink bindings."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


INTERFACE_DECL = re.compile(r"const\s+WrapperTypeInfo\s+V8(?P<binding>[A-Za-z0-9_]+)::wrapper_type_info_\s*\{")
FUNCTION_DECL = re.compile(r"(?:void|v8::Local\s*<\s*v8::Value\s*>)\s+(?P<name>[A-Za-z0-9_]+Callback)\s*\([^)]*\)\s*\{")
TABLE_DECL = re.compile(r"static\s+const\s+IDLMemberInstaller::(?P<kind>Attribute|Operation|ConstantValue)Config\s+(?P<name>k[A-Za-z0-9_]+Table)\s*\[\]\s*=\s*\{")
CONSTRUCTOR_CALLBACK = re.compile(r"[A-Za-z0-9_]+ConstructorCallback")
IDL_INTERFACE_DECL = re.compile(
    r"(?:(?P<attrs>\[[\s\S]*?\])\s*)?interface(?:\s+mixin)?\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*(?P<parent>[A-Za-z_][A-Za-z0-9_]*))?\s*\{")

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHROMIUM_ROOT = Path(
    os.environ.get("CHROMIUM_ROOT", PROJECT_ROOT / ".chromium-build" / "chromium")
)


def _skip_string(text, index):
    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\": index += 2
        elif text[index] == quote: return index + 1
        else: index += 1
    return index


def balanced_end(text, start, opening="{", closing="}"):
    if start >= len(text) or text[start] != opening:
        raise ValueError("unbalanced block")
    depth = 0; index = start
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            index = _skip_string(text, index); continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2); index = len(text) if newline < 0 else newline + 1; continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2); index = len(text) if end < 0 else end + 2; continue
        if char == opening: depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0: return index + 1
        index += 1
    raise ValueError("unterminated block")


def split_top_level(text, separator=","):
    result, start, stack, index = [], 0, [], 0
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            index = _skip_string(text, index); continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2); index = len(text) if newline < 0 else newline + 1; continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2); index = len(text) if end < 0 else end + 2; continue
        if char in "({[<": stack.append(char)
        elif char in "}])>" and stack: stack.pop()
        elif char == separator and not stack:
            result.append(text[start:index].strip()); start = index + 1
        index += 1
    result.append(text[start:].strip())
    return result


def _strip_unsigned(value):
    value = value.strip()
    value = re.sub(r"^unsigned\s*\(\s*", "", value)
    return re.sub(r"\s*\)$", "", value).strip()


def _flag(value, prefix):
    match = re.search(re.escape(prefix) + r"::k([A-Za-z0-9_]+)", _strip_unsigned(value))
    return match.group(1) if match else None


def _table_entries(text, table_start):
    end = balanced_end(text, table_start); body = text[table_start + 1:end - 1]; entries = []; index = 0
    while index < len(body):
        if body[index] == "{":
            entry_end = balanced_end(body, index); fields = split_top_level(body[index + 1:entry_end - 1])
            if fields and fields[0].startswith('"'): entries.append(fields)
            index = entry_end
        else: index += 1
    return entries


def _find_functions(text):
    functions = {}
    for match in FUNCTION_DECL.finditer(text):
        try: end = balanced_end(text, text.find("{", match.start()))
        except ValueError: continue
        functions[match.group("name")] = text[match.end():end - 1]
    return functions


def _argument_types(body):
    result = {}
    trait = re.compile(r"NativeValueTraits\s*<")
    for match in trait.finditer(body):
        depth, index = 1, match.end()
        while index < len(body) and depth:
            if body[index] == "<": depth += 1
            elif body[index] == ">": depth -= 1
            index += 1
        if depth: continue
        type_name = body[match.end():index - 1].strip(); conversion = body[index:index + 80]
        if not re.match(r"::(?:ArgumentValue|NativeValue)", conversion): continue
        info = re.search(r"info\s*\[\s*(\d+)\s*\]", body[index:index + 700])
        if not info: continue
        argument_index = int(info.group(1)); parameter = result.setdefault(argument_index, {
            "index": argument_index, "idl_type": type_name, "optional": False,
            "nullable": False, "default": None, "review": "generated-callback",
        })
        before = body[max(0, match.start() - 180):match.start()]; after = body[index:min(len(body), index + 500)]
        parameter["optional"] |= "IsUndefined" in before or "IsUndefined" in after
        parameter["optional"] |= type_name.startswith("IDLOptional<")
        parameter["nullable"] |= "Nullable" in type_name or "IsNull" in before or "IsNull" in after
        default = re.search(r"\{\s*([^{};]+)\s*\}", after)
        if default and parameter["optional"]: parameter["default"] = default.group(1).strip()
    return [result[key] for key in sorted(result)]


def extract_callback_signatures(text):
    signatures = {}
    for name, body in _find_functions(text).items():
        required = re.search(r"NotEnoughArguments\s*\(\s*(\d+)", body)
        parameters = _argument_types(body)
        signatures[name] = {
            "required": int(required.group(1)) if required else 0,
            "parameters": parameters,
            "status": "generated",
            "source_evidence": "NotEnoughArguments/NativeValueTraits in generated callback",
        }
    return signatures


def _member_from_table(kind, fields, signatures):
    if len(fields) < 2: return None
    name = fields[0].strip().strip('"')
    if kind == "Attribute":
        if len(fields) < 8: return {"name": name, "kind": "attribute", "review": "manual-review-required", "raw": fields}
        location = _flag(fields[4], "IDLMemberInstaller::FlagLocation") or "Unknown"
        setter = None if fields[2].strip() == "nullptr" else fields[2].strip()
        result = {"name": name, "kind": "attribute", "getter": fields[1].strip(),
                "setter": setter,
                "readonly": fields[2].strip() == "nullptr", "location": location.lower(),
                "flags": _strip_unsigned(fields[3]), "world": _flag(fields[5], "IDLMemberInstaller::FlagWorld"),
                "receiver": _flag(fields[6], "IDLMemberInstaller::FlagReceiverCheck"),
                "cross_origin": _flag(fields[7], "IDLMemberInstaller::FlagCrossOriginCheck"),
                "side_effect": _flag(fields[8], "v8::SideEffectType") if len(fields) > 8 else None,
                "cached_accessor": _flag(fields[9], "V8PrivateProperty::CachedAccessor") if len(fields) > 9 else None}
        if setter:
            result["setter_signature"] = signatures.get(setter, {
                "required": 1, "parameters": [], "status": "unresolved",
                "source_evidence": "attribute table without parseable setter callback",
            })
            if not result["setter_signature"].get("parameters"):
                result["setter_signature"]["status"] = "unresolved"
        return result
    if kind == "Operation":
        if len(fields) < 6: return {"name": name, "kind": "operation", "review": "manual-review-required", "raw": fields}
        callback, length = fields[1].strip(), int(fields[2].strip()); location = _flag(fields[4], "IDLMemberInstaller::FlagLocation") or "Unknown"
        signature = dict(signatures.get(callback, {
            "required": length, "parameters": [], "status": "unresolved",
            "source_evidence": "operation table without parseable callback",
        }))
        if length and len(signature.get("parameters") or ()) < length:
            signature["required"] = length
            signature["status"] = "unresolved"
        return {"name": name, "kind": "operation", "callback": callback, "length": length, "location": location.lower(),
                "flags": _strip_unsigned(fields[3]), "world": _flag(fields[5], "IDLMemberInstaller::FlagWorld"),
                "receiver": _flag(fields[6], "IDLMemberInstaller::FlagReceiverCheck") if len(fields) > 6 else None,
                "cross_origin": _flag(fields[7], "IDLMemberInstaller::FlagCrossOriginCheck") if len(fields) > 7 else None,
                "side_effect": _flag(fields[8], "v8::SideEffectType") if len(fields) > 8 else None,
                "signature": signature}
    return {"name": name, "kind": "constant", "value": fields[1].strip(), "location": "interface", "status": "unresolved"}


def extract_members(text):
    signatures = extract_callback_signatures(text); members = {"instance": [], "prototype": [], "interface": []}
    for match in TABLE_DECL.finditer(text):
        try: entries = _table_entries(text, text.find("{", match.start()))
        except ValueError: continue
        for fields in entries:
            member = _member_from_table(match.group("kind"), fields, signatures)
            if member is not None: members.setdefault(member.get("location", "interface"), []).append(member)
    return members


def _primitive_constant(expression):
    """Return a JSON-safe literal when Chromium emitted one, else None."""
    value = expression.strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    if re.fullmatch(r"[-+]?0[xX][0-9A-Fa-f]+", value):
        try:
            return int(value, 0)
        except ValueError:
            return None
    if value in ("true", "false"):
        return value == "true"
    match = re.fullmatch(r'"((?:\\.|[^"\\])*)"', value)
    if match:
        try:
            return json.loads(value)
        except Exception:
            return match.group(1)
    return None


def extract_template(text):
    """Extract V8 template switches and interceptor callback names.

    Chromium changes formatting frequently, so this intentionally records
    conservative evidence rather than attempting to parse C++ expressions.
    """
    template = {
        "immutable_proto": bool(re.search(r"SetImmutableProto\s*\(", text)),
        "undetectable": bool(re.search(r"MarkAsUndetectable\s*\(", text)),
        "global_this": bool(re.search(r"SetAccessCheckCallback[^;]*(?:global_this|GlobalThis)", text, re.I)),
        "as_object": bool(re.search(r"SetPrototype\s*\([^;]*ObjectPrototype", text)),
        "unconstructable": bool(re.search(r"(?:Set(?:Call|Construct)Handler|Unconstructable)[^;]*kDontAll", text)),
        "array": bool(re.search(r"SetIntrinsicDataProperty[^;]*Array|ArrayPrototype", text)),
        "call_handler": None,
        "access_check": None,
    }
    call = re.search(r"SetCallAsFunctionHandler\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)", text)
    if call:
        template["call_handler"] = call.group(1)
    access = re.search(r"SetAccessCheckCallback(?:AndHandler)?\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)", text)
    if access:
        template["access_check"] = {
            "callback": access.group(1),
            "with_handler": bool(re.search(r"SetAccessCheckCallbackAndHandler\s*\(", text)),
        }
    handlers = {"named": {}, "indexed": {}, "cross_origin": {"named": {}, "indexed": {}}}
    # Both NamedPropertyHandlerConfiguration and IndexedPropertyHandlerConfiguration
    # use a stable callback tuple: getter, setter, query, deleter, enumerator.
    for kind, label in (("named", "NamedPropertyHandlerConfiguration"), ("indexed", "IndexedPropertyHandlerConfiguration")):
        for match in re.finditer(label + r"\s*\(([^;]*?)\)", text, re.S):
            args = split_top_level(match.group(1))
            names = ("get", "set", "query", "del", "enum", "definer")
            data = {}
            for index, callback in enumerate(args[:len(names)]):
                callback = callback.strip()
                if callback and callback != "nullptr":
                    data[names[index]] = callback
            if data:
                handlers[kind] = data
    # Cross-origin handlers are commonly named in generated helper functions.
    for kind in ("named", "indexed"):
        for role in ("get", "set", "query", "del", "enum", "definer"):
            patterns = [
                rf"CrossOrigin{kind.title()}[^\\n]*{role}",
                rf"{kind.title()}Property[^\\n]*CrossOrigin[^\\n]*{role}",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    callback = re.search(r"([A-Za-z_][A-Za-z0-9_]*Callback)", match.group(0))
                    if callback:
                        handlers["cross_origin"][kind][role] = callback.group(1)
                        break
    template["handlers"] = handlers
    return template


def extract_constants(members):
    """Normalize constant table entries with literal/expression dual form."""
    constants = []
    for location, values in members.items():
        for member in values:
            if member.get("kind") != "constant":
                continue
            expression = str(member.get("value", ""))
            item = {"name": member.get("name"), "expression": expression,
                    "resolved": _primitive_constant(expression) is not None,
                    "value": _primitive_constant(expression), "location": location}
            constants.append(item)
    return constants


def _callback_roles(item):
    roles = {}
    constructor = item.get("constructor") or {}
    signature = constructor.get("signature") or {}
    # Callback names are retained even when their body could not be decoded.
    if constructor.get("callback"):
        roles.setdefault(constructor["callback"], set()).add("constructor")
    for values in (item.get("members") or {}).values():
        for member in values:
            callback = member.get("callback")
            if callback:
                roles.setdefault(callback, set()).add(member.get("kind", "member"))
            setter = member.get("setter")
            if setter:
                roles.setdefault(setter, set()).add("setter")
            getter = member.get("getter")
            if getter:
                roles.setdefault(getter, set()).add("getter")
    return {name: {"roles": sorted(values)} for name, values in roles.items()}


def _merge_members(target, incoming):
    for location, entries in incoming.items():
        known = {(item.get("kind"), item.get("name"), item.get("location")) for item in target[location]}
        for entry in entries:
            key = (entry.get("kind"), entry.get("name"), entry.get("location"))
            if key not in known: target[location].append(entry); known.add(key)
            elif entry.get("kind") == "operation":
                existing = next(item for item in target[location] if (item.get("kind"), item.get("name"), item.get("location")) == key)
                if existing.get("callback") != entry.get("callback"): existing.setdefault("overloads", []).append(entry)


def _merge_template(current, incoming):
    """Merge template evidence from split generated binding fragments."""
    result = dict(current or {})
    for key, value in (incoming or {}).items():
        if key == "handlers":
            handlers = result.setdefault("handlers", {})
            for group, entries in (value or {}).items():
                if isinstance(entries, dict):
                    handlers.setdefault(group, {}).update(entries)
                elif entries:
                    handlers[group] = entries
        elif isinstance(value, bool):
            result[key] = bool(result.get(key)) or value
        elif value is not None:
            result[key] = value
    return result


def _canonical_declaration(text, match):
    end = balanced_end(text, text.find("{", match.start())); body = text[text.find("{", match.start()) + 1:end - 1]
    tag = (re.search(r'"([A-Za-z0-9_]+)"', body) or [None, match.group("binding")])[1]
    parent_match = re.search(r"V8([A-Za-z0-9_]+)::GetWrapperTypeInfo\(\)", body)
    has_constructor = bool(CONSTRUCTOR_CALLBACK.search(text))
    # Blink interface constructors are created with V8's throwing constructor
    # behavior. Legacy factory functions are separate exposed functions and do
    # not make the interface constructor callable.
    allow_call = False if has_constructor else None
    return tag, parent_match.group(1) if parent_match else None, has_constructor, allow_call


def _exposure(text):
    result = []
    if re.search(r"IsWindow\(\)", text): result.append("Window")
    if re.search(r"Is(?:Dedicated|Shared)?WorkerGlobalScope\(\)", text): result.append("Worker")
    return result


def _idl_metadata(root):
    """Read Blink IDL extended attributes needed by V8 template setup."""
    metadata = {}
    for path in sorted(root.rglob("*.idl")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in IDL_INTERFACE_DECL.finditer(text):
            try:
                end = balanced_end(text, text.find("{", match.start()))
            except ValueError:
                continue
            attrs, body = match.group("attrs"), text[text.find("{", match.start()) + 1:end - 1]
            attrs = attrs or ""
            exposure_match = re.search(r"\bExposed\s*=\s*(?:\(([^)]*)\)|([A-Za-z_][A-Za-z0-9_]*))", attrs)
            exposed_in = []
            if exposure_match:
                exposed_in = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", exposure_match.group(1) or exposure_match.group(2))
            constants = {}
            for constant in re.finditer(r"\bconst\s+[A-Za-z_][A-Za-z0-9_<>? ]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+)", body):
                expression = constant.group(2).strip()
                constants[constant.group(1)] = {"expression": expression,
                                                "value": _primitive_constant(expression),
                                                "resolved": _primitive_constant(expression) is not None}
            metadata[match.group("name")] = {
                "parent": match.group("parent"),
                "exposed_in": exposed_in,
                "exposed": (bool(exposed_in) and "LegacyNoInterfaceObject" not in attrs)
                if exposure_match or "LegacyNoInterfaceObject" in attrs else None,
                "allow_new": bool(re.search(r"\bconstructor\s*\(", body)),
                "allow_call": bool(re.search(r"\bLegacyCallAsFunction\b", attrs)),
                "constants": constants,
            }
    return metadata


def _apply_idl_metadata(extracted, metadata):
    for name, item in extracted.items():
        if name.startswith("_") or name not in metadata:
            continue
        info = metadata[name]
        if info["parent"]:
            item["parent"] = info["parent"]
        if info["exposed_in"]:
            item["exposed_in"] = info["exposed_in"]
        if info["exposed"] is not None:
            item["constructor"]["exposed"] = info["exposed"]
        # Generated C++ constructor callbacks are authoritative; IDL metadata
        # only fills gaps and must not erase a callback discovered in source.
        item["constructor"]["allow_new"] = bool(item["constructor"].get("allow_new") or info["allow_new"])
        item["constructor"]["allow_call"] = bool(item["constructor"].get("allow_call") or info["allow_call"])
        item["constructible"] = bool(item["constructor"].get("allow_new") or item["constructor"].get("allow_call"))
        if info.get("constants"):
            known = {entry.get("name"): entry for entry in item.get("constants", ())}
            for constant_name, constant in info["constants"].items():
                known[constant_name] = {"name": constant_name, "location": "interface", **constant}
            item["constants"] = list(known.values())
            for member in item.get("members", {}).get("interface", ()):
                if member.get("kind") == "constant" and member.get("name") in info["constants"]:
                    member.update(info["constants"][member["name"]])


def _filename_interface(path, text):
    """Infer the canonical interface for module-only binding fragments."""
    match = re.search(r"V8([A-Za-z0-9_]+)::(?:Impl::|Install)", text)
    if match:
        binding = match.group(1)
        # Generated aliases such as DOMURL are represented by V8URL.
        if binding.startswith("DOM") and len(binding) > 3:
            binding = binding[3:]
        return binding
    stem = path.stem
    if stem.startswith("v8_"):
        return "".join(part[:1].upper() + part[1:] for part in stem[3:].split("_"))
    return None


def extract(root, idl_root=None):
    result, parse_failures, duplicate_sources = {}, [], 0
    for path in sorted(root.rglob("v8_*.cc")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        declarations = list(INTERFACE_DECL.finditer(text))
        if not declarations:
            inferred = _filename_interface(path, text)
            if inferred and inferred in result:
                item = result[inferred]
                relative = str(path).replace("/", "\\")
                if relative not in item["sources"]:
                    item["sources"].append(relative)
                    duplicate_sources += 1
                _merge_members(item["members"], extract_members(text))
                item["exposed_in"] = sorted(set(item.get("exposed_in", ())) | set(_exposure(text)))
                item["template"] = _merge_template(item.get("template") or {}, extract_template(text))
            continue
        for declaration in declarations:
            try:
                name, parent, constructible, allow_call = _canonical_declaration(text, declaration)
                members = extract_members(text)
                callback_signatures = extract_callback_signatures(text)
            except (ValueError, TypeError): parse_failures.append(str(path)); continue
            relative = str(path).replace("/", "\\"); item = result.get(name)
            if item is None:
                exposure = _exposure(text)
                constructor_callback = next((callback for callback in callback_signatures if callback.endswith("ConstructorCallback")), None)
                item = result[name] = {"parent": parent, "source": relative, "sources": [relative], "snapshot": "chromium-generated-bindings",
                    "constructible": constructible, "constructor": {"exposed": bool(exposure), "allow_new": constructible,
                    "allow_call": allow_call, "length": next((sig.get("required", 0) for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), None),
                    "signature": next((sig for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), {"status": "unresolved", "parameters": [], "source_evidence": "constructor callback not found"}),
                    "status": "generated" if any(callback.endswith("ConstructorCallback") for callback in callback_signatures) else "unresolved",
                    "callback": constructor_callback},
                    "tag": name, "members": {"instance": [], "prototype": [], "interface": []}, "mixins": [], "exposed_in": _exposure(text)}
                item["identity"] = {"name": name, "parent": parent, "tag": name,
                                    "exposed_in": _exposure(text), "exported_constructor": bool(exposure and constructible)}
                item["template"] = extract_template(text)
            else:
                duplicate_sources += 1
                if relative not in item["sources"]: item["sources"].append(relative)
                if parent and not item.get("parent"): item["parent"] = parent
                item["constructible"] |= constructible; item["constructor"]["exposed"] |= bool(_exposure(text)); item["constructor"]["allow_new"] |= constructible; item["constructor"]["allow_call"] |= allow_call
                item["exposed_in"] = sorted(set(item.get("exposed_in", ())) | set(_exposure(text)))
                item.setdefault("identity", {}).update({"exposed_in": item["exposed_in"], "exported_constructor": bool(item["constructor"].get("exposed") and item["constructor"].get("allow_new"))})
                item["template"] = _merge_template(item.get("template") or {}, extract_template(text))
            _merge_members(item["members"], members)
    for item in result.values():
        if not isinstance(item, dict):
            continue
        item["sources"] = sorted(set(item["sources"]))
        if not item["exposed_in"]: item.pop("exposed_in", None)
        item.setdefault("identity", {}).update({"name": item.get("tag"), "parent": item.get("parent"),
                                                  "tag": item.get("tag"), "exposed_in": item.get("exposed_in", ())})
        item["identity"]["exported_constructor"] = bool((item.get("constructor") or {}).get("exposed") and (item.get("constructor") or {}).get("allow_new"))
        extracted_constants = {entry.get("name"): entry for entry in extract_constants(item.get("members") or {})}
        extracted_constants.update({entry.get("name"): entry for entry in item.get("constants", ())})
        item["constants"] = list(extracted_constants.values())
        item["handlers"] = (item.get("template") or {}).get("handlers", {"named": {}, "indexed": {}, "cross_origin": {"named": {}, "indexed": {}}})
        item["callbacks"] = _callback_roles(item)
    if idl_root is not None:
        _apply_idl_metadata(result, _idl_metadata(Path(idl_root)))
        for name, item in result.items():
            if name.startswith("_") or not isinstance(item, dict):
                continue
            item.setdefault("identity", {})["exported_constructor"] = bool(
                (item.get("constructor") or {}).get("exposed") and
                (item.get("constructor") or {}).get("allow_new")
            )
    result["_report"] = _report(result, duplicate_sources, parse_failures)
    return result


def _signature_status(signature):
    return (signature or {}).get("status", "unresolved")


def _report(result, duplicate_sources=0, parse_failures=()):
    interfaces = {name: item for name, item in result.items() if not name.startswith("_")}
    signatures = []
    for item in interfaces.values():
        signatures.append((item.get("constructor") or {}).get("signature") or {})
        for values in item["members"].values():
            for member in values:
                if member.get("kind") == "operation":
                    signatures.append(member.get("signature") or {})
                elif member.get("kind") == "attribute" and member.get("setter"):
                    signatures.append(member.get("setter_signature") or {})
    return {
        "interfaces": len(interfaces),
        "member_total": sum(sum(len(values) for values in item["members"].values()) for item in interfaces.values()),
        "interfaces_with_members": sum(any(item["members"].values()) for item in interfaces.values()),
        "interfaces_with_signatures": sum(any(member.get("signature") for values in item["members"].values() for member in values) for item in interfaces.values()),
        "generated_signatures": sum(_signature_status(signature) == "generated" for signature in signatures),
        "reviewed_signatures": sum(_signature_status(signature) == "manual-reviewed" for signature in signatures),
        "unresolved_signatures": sum(_signature_status(signature) == "unresolved" for signature in signatures),
        "manual_review_required": sum(_signature_status(signature) == "unresolved" for signature in signatures),
        "duplicate_sources": duplicate_sources,
        "parse_failures": sorted(set(parse_failures)),
    }


def _review_source_path(source):
    """Normalize generated source paths for review evidence comparisons."""
    normalized = str(source).replace("\\", "/")
    if "/renderer/" in normalized:
        normalized = normalized.split("/renderer/", 1)[1]
    if normalized.startswith("bindings/"):
        normalized = normalized[len("bindings/"):]
    normalized = normalized.replace("/v8/v8_", "/v8_")
    return normalized


def apply_reviews(extracted, review_path):
    """Merge generation-only reviewed signatures into a renderer snapshot."""
    reviews = json.loads(Path(review_path).read_text(encoding="utf-8"))
    interfaces = {name: item for name, item in extracted.items() if not name.startswith("_")}
    for interface_name, signature in (reviews.get("constructors") or {}).items():
        if interface_name not in interfaces:
            raise ValueError(f"stale Web IDL constructor review: {interface_name}")
        reviewed = dict(signature)
        expected_sources = reviewed.pop("target_sources", None)
        actual_sources = sorted(_review_source_path(source) for source in interfaces[interface_name].get("sources", ()))
        if expected_sources is None or sorted(expected_sources) != actual_sources:
            raise ValueError(f"stale Web IDL constructor source evidence: {interface_name}")
        constructor_metadata = {
            key: reviewed.pop(key) for key in ("exposed", "allow_new", "allow_call", "length")
            if key in reviewed
        }
        reviewed["status"] = "manual-reviewed"
        constructor = interfaces[interface_name]["constructor"]
        constructor.update(constructor_metadata)
        interfaces[interface_name]["constructible"] = bool(
            constructor.get("allow_new") or constructor.get("allow_call")
        )
        constructor["signature"] = reviewed
        constructor["status"] = "manual-reviewed"
        if constructor.get("length") is None:
            constructor["length"] = int(reviewed.get("required") or 0)
    for key, signature in (reviews.get("members") or {}).items():
        interface_name, separator, member_name = key.partition(".")
        if not separator or interface_name not in interfaces:
            raise ValueError(f"stale Web IDL member review: {key}")
        reviewed = dict(signature)
        expected_callbacks = reviewed.pop("target_callbacks", None)
        reviewed["status"] = "manual-reviewed"
        matches = []
        candidates = [interface_name]
        for candidate_name, item in interfaces.items():
            cursor, seen = candidate_name, set()
            while cursor and cursor not in seen:
                seen.add(cursor)
                if cursor == interface_name:
                    candidates.append(candidate_name)
                    break
                cursor = interfaces.get(cursor, {}).get("parent")
        for candidate_name in dict.fromkeys(candidates):
            for location, values in interfaces[candidate_name]["members"].items():
                matches.extend(
                    (candidate_name, location, member) for member in values
                    if member.get("name") == member_name and member.get("kind") in ("operation", "attribute")
                )
        if not matches:
            raise ValueError(f"stale Web IDL member review: {key}")
        actual_callbacks = sorted(
            f"{candidate_name}|{location}|{member.get('kind')}|{member.get('callback') or member.get('setter')}"
            for candidate_name, location, member in matches
        )
        if expected_callbacks is None or not set(expected_callbacks).issubset(actual_callbacks):
            raise ValueError(f"stale Web IDL member callback evidence: {key}")
        for _candidate_name, _location, member in matches:
            if member.get("kind") == "attribute":
                if not member.get("setter"):
                    continue
                member["setter_signature"] = dict(reviewed)
            else:
                member["signature"] = dict(reviewed)
    previous = extracted.get("_report") or {}
    extracted["_report"] = _report(
        extracted,
        previous.get("duplicate_sources", 0),
        previous.get("parse_failures", ()),
    )
    return extracted


def _compact_signature(signature):
    """Keep only the signature data consumed by :mod:`pyv8c.bindings.webidl`.

    Extraction retains callback and source evidence until reviews have been
    applied.  Those fields are useful for producing a baseline, but do not
    participate in runtime binding and used to dominate the packaged JSON.
    """
    signature = signature or {}
    result = {}
    status = signature.get("status") or signature.get("review")
    if status == "manual-reviewed":
        result["s"] = "m"
    elif status != "generated":
        result["s"] = "u"
    if "required" in signature:
        result["r"] = int(signature["required"])
    if signature.get("max_args") is not None:
        result["x"] = int(signature["max_args"])
    if signature.get("variadic"):
        result["v"] = 1
    if signature.get("error_mode") and signature["error_mode"] != "throw":
        result["e"] = signature["error_mode"]
    parameters = []
    for parameter in signature.get("parameters") or ():
        item = {"t": parameter.get("type", parameter.get("idl_type", "any"))}
        if parameter.get("optional"):
            item["o"] = 1
            # A default is observed only for an optional undefined argument.
            # Preserve it there, but discard the extractor's irrelevant
            # default:null entries for required parameters.
            if "default" in parameter:
                item["d"] = parameter["default"]
        if parameter.get("nullable"):
            item["n"] = 1
        if parameter.get("variadic"):
            item["v"] = 1
        if parameter.get("enum"):
            item["e"] = list(parameter["enum"])
        parameters.append(item)
    if parameters:
        result["p"] = parameters
    if signature.get("overloads"):
        result["a"] = [_compact_signature(item) for item in signature["overloads"]]
    return result


def _compact_member_options(member):
    """Encode only deviations from pyv8c's native-template defaults."""
    result = {}
    flags = str(member.get("flags") or "")
    if flags and flags != "v8::None":
        result["f"] = flags
    if member.get("world") not in (None, "AllWorlds"):
        result["w"] = member["world"]
    if member.get("receiver") not in (None, "Check"):
        result["r"] = member["receiver"]
    if member.get("cross_origin") not in (None, "Check"):
        result["x"] = member["cross_origin"]
    if member.get("side_effect") not in (None, "HasSideEffect"):
        result["e"] = member["side_effect"]
    return result


def compact_baseline(extracted):
    """Produce schema 4, the compact runtime representation.

    Schema 3 is an extraction/review representation.  Schema 4 intentionally
    omits source paths, callback names, getter names, duplicate tags and other
    generation-only metadata while preserving every field used to configure a
    public runtime member.
    """
    output = {}
    for name, item in extracted.items():
        if name.startswith("_"):
            continue
        compact = {}
        if item.get("parent"):
            compact["p"] = item["parent"]
        if item.get("exposed_in"):
            compact["e"] = item["exposed_in"]
        constructor = item.get("constructor") or {}
        if constructor.get("allow_new") or constructor.get("allow_call"):
            compact["c"] = [
                int(constructor.get("length") or 0),
                _compact_signature(constructor.get("signature")),
                1 if constructor.get("allow_call") else 0,
            ]
        members = {}
        for location, values in (item.get("members") or {}).items():
            grouped = {}
            for member in values:
                kind = member.get("kind")
                options = _compact_member_options(member)
                if kind == "operation":
                    value = [member["name"], int(member.get("length") or 0),
                             _compact_signature(member.get("signature"))]
                    if options:
                        value.append(options)
                    grouped.setdefault("o", []).append(value)
                elif kind == "attribute":
                    # All entries have getters.  A missing setter is exactly
                    # the read-only state needed by the runtime.
                    setter = member.get("setter")
                    value = [member["name"],
                             _compact_signature(member.get("setter_signature")) if setter else None]
                    if options:
                        value.append(options)
                    grouped.setdefault("a", []).append(value)
                elif kind == "constant":
                    grouped.setdefault("c", []).append([member["name"], member.get("value")])
            if grouped:
                members[{"instance": "i", "prototype": "p", "interface": "s"}[location]] = grouped
        if members:
            compact["m"] = members
        output[name] = compact
    return output


def schema5_baseline(extracted):
    """Build the full runtime snapshot consumed by the dynamic class loader."""
    output = {}
    for name, source in extracted.items():
        if name.startswith("_"):
            continue
        item = json.loads(json.dumps(source))
        identity = item.setdefault("identity", {})
        identity.setdefault("name", name)
        identity.setdefault("parent", item.get("parent"))
        identity.setdefault("tag", item.get("tag") or name)
        identity.setdefault("exposed_in", item.get("exposed_in", []))
        constructor = item.setdefault("constructor", {})
        constructor.setdefault("exported", bool(identity.get("exported_constructor")))
        template = item.setdefault("template", extract_template(""))
        template.setdefault("handlers", item.get("handlers") or {"named": {}, "indexed": {}, "cross_origin": {"named": {}, "indexed": {}}})
        item.setdefault("constants", extract_constants(item.get("members") or {}))
        item.setdefault("callbacks", _callback_roles(item))
        # Source paths are useful for review but do not duplicate source text.
        item["provenance"] = {"sources": sorted(set(item.get("sources", []))),
                              "snapshot": item.get("snapshot", "chromium-generated-bindings")}
        output[name] = item
    return output


def generated_bindings_dir(build_dir: Path) -> Path:
    """Return Chromium's generated Blink V8 binding directory."""
    candidates = (
        build_dir / "gen" / "third_party" / "blink" / "renderer" / "bindings",
        build_dir / "gen" / "third_party" / "blink" / "renderer",
    )
    for candidate in candidates:
        if any(candidate.rglob("v8_*.cc")):
            return candidate
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description="Parse Chromium-generated Blink V8 bindings into an interface baseline.")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="output path; legacy form also accepts <bindings-dir> <output>")
    parser.add_argument("--chromium-root", type=Path, default=DEFAULT_CHROMIUM_ROOT,
                        help="Chromium checkout root (default: %(default)s)")
    parser.add_argument("--build-dir", type=Path, default=None,
                        help="GN output directory (default: <chromium-root>/src/out/Cronet145)")
    parser.add_argument("--bindings-dir", type=Path, default=None,
                        help="existing generated bindings directory")
    parser.add_argument("--reviews", type=Path, default=PROJECT_ROOT / "tools" / "webidl-reviews.json")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if len(args.paths) not in (1, 2):
        parser.error("provide <output> or the legacy <bindings-dir> <output> form")
    build_dir = args.build_dir or (args.chromium_root / "src" / "out" / "Cronet145")
    legacy_renderer = args.paths[0] if len(args.paths) == 2 else None
    output_path = args.paths[-1]
    renderer = args.bindings_dir or legacy_renderer or generated_bindings_dir(build_dir)
    if not any(renderer.rglob("v8_*.cc")):
        parser.error(f"generated Blink bindings not found under {renderer}; run fetch_chromium.py first")
    if not any(renderer.rglob("v8_*.cc")):
        parser.error(f"binding generation completed but no v8_*.cc files were found under {renderer}")
    extracted = extract(renderer, args.chromium_root / "src" / "third_party" / "blink" / "renderer")
    if args.reviews:
        apply_reviews(extracted, args.reviews)
    report = extracted.pop("_report")
    output = {"_webidl": {"schema": 5, "source": "tools/extract_interfaces.py",
        "renderer_snapshot": str(renderer), "report": report}, **schema5_baseline(extracted)}
    # The published snapshot is machine-consumed. Compact serialization avoids
    # carrying indentation overhead on top of the compact schema itself.
    output_path.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    if args.report: print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__": main()
