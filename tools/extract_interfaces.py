"""Extract a reviewable interface snapshot from generated Blink bindings."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


INTERFACE_DECL = re.compile(r"const\s+WrapperTypeInfo\s+V8(?P<binding>[A-Za-z0-9_]+)::wrapper_type_info_\s*\{")
FUNCTION_DECL = re.compile(r"(?:void|v8::Local\s*<\s*v8::Value\s*>)\s+(?P<name>[A-Za-z0-9_]+Callback)\s*\([^)]*\)\s*\{")
TABLE_DECL = re.compile(r"static\s+const\s+IDLMemberInstaller::(?P<kind>Attribute|Operation|ConstantValue)Config\s+(?P<name>k[A-Za-z0-9_]+Table)\s*\[\]\s*=\s*\{")
CONSTRUCTOR_CALLBACK = re.compile(r"[A-Za-z0-9_]+ConstructorCallback")
IDL_INTERFACE_DECL = re.compile(
    r"(?:(?P<attrs>\[[\s\S]*?\])\s*)?interface(?:\s+mixin)?\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*(?P<parent>[A-Za-z_][A-Za-z0-9_]*))?\s*\{")

DEFAULT_CHROMIUM_ROOT = Path(os.environ.get("CHROMIUM_ROOT", r"D:\src\chromium"))


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


def _merge_members(target, incoming):
    for location, entries in incoming.items():
        known = {(item.get("kind"), item.get("name"), item.get("location")) for item in target[location]}
        for entry in entries:
            key = (entry.get("kind"), entry.get("name"), entry.get("location"))
            if key not in known: target[location].append(entry); known.add(key)
            elif entry.get("kind") == "operation":
                existing = next(item for item in target[location] if (item.get("kind"), item.get("name"), item.get("location")) == key)
                if existing.get("callback") != entry.get("callback"): existing.setdefault("overloads", []).append(entry)


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
            metadata[match.group("name")] = {
                "parent": match.group("parent"),
                "exposed_in": exposed_in,
                "exposed": (bool(exposed_in) and "LegacyNoInterfaceObject" not in attrs)
                if exposure_match or "LegacyNoInterfaceObject" in attrs else None,
                "allow_new": bool(re.search(r"\bconstructor\s*\(", body)),
                "allow_call": bool(re.search(r"\bLegacyCallAsFunction\b", attrs)),
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
        item["constructor"]["allow_new"] = info["allow_new"]
        item["constructor"]["allow_call"] = info["allow_call"]
        item["constructible"] = info["allow_new"]


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
                item = result[name] = {"parent": parent, "source": relative, "sources": [relative], "snapshot": "chromium-generated-bindings",
                    "constructible": constructible, "constructor": {"exposed": bool(exposure), "allow_new": constructible,
                    "allow_call": allow_call, "length": next((sig.get("required", 0) for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), None),
                    "signature": next((sig for callback, sig in callback_signatures.items() if callback.endswith("ConstructorCallback")), {"status": "unresolved", "parameters": [], "source_evidence": "constructor callback not found"}),
                    "status": "generated" if any(callback.endswith("ConstructorCallback") for callback in callback_signatures) else "unresolved"},
                    "tag": name, "members": {"instance": [], "prototype": [], "interface": []}, "mixins": [], "exposed_in": _exposure(text)}
            else:
                duplicate_sources += 1
                if relative not in item["sources"]: item["sources"].append(relative)
                if parent and not item.get("parent"): item["parent"] = parent
                item["constructible"] |= constructible; item["constructor"]["exposed"] |= bool(_exposure(text)); item["constructor"]["allow_new"] |= constructible; item["constructor"]["allow_call"] |= allow_call
                item["exposed_in"] = sorted(set(item.get("exposed_in", ())) | set(_exposure(text)))
            _merge_members(item["members"], members)
    for item in result.values():
        item["sources"] = sorted(set(item["sources"]))
        if not item["exposed_in"]: item.pop("exposed_in", None)
    if idl_root is not None:
        _apply_idl_metadata(result, _idl_metadata(Path(idl_root)))
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


def apply_reviews(extracted, review_path):
    """Merge generation-only reviewed signatures into a renderer snapshot."""
    reviews = json.loads(Path(review_path).read_text(encoding="utf-8"))
    interfaces = {name: item for name, item in extracted.items() if not name.startswith("_")}
    for interface_name, signature in (reviews.get("constructors") or {}).items():
        if interface_name not in interfaces:
            raise ValueError(f"stale Web IDL constructor review: {interface_name}")
        reviewed = dict(signature)
        expected_sources = reviewed.pop("target_sources", None)
        actual_sources = sorted(
            source.replace("\\", "/").split("/renderer/", 1)[-1]
            for source in interfaces[interface_name].get("sources", ())
        )
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
        if expected_callbacks is None or sorted(expected_callbacks) != actual_callbacks:
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


def build_bindings(chromium_root: Path, build_dir: Path, target: str = "blink_generate_bindings_all"):
    """Generate Blink binding sources using the checkout's configured toolchain."""
    depot_tools = next((path for path in (
        chromium_root / "depot_tools", chromium_root.parent / "depot_tools") if path.is_dir()), chromium_root / "depot_tools")
    autoninja = shutil.which("autoninja") or shutil.which("autoninja.bat")
    if autoninja is None:
        for candidate in (depot_tools / "autoninja.bat", depot_tools / "autoninja"):
            if candidate.is_file():
                autoninja = str(candidate)
                break
    if autoninja is None:
        raise FileNotFoundError("autoninja not found; add D:\\src\\depot_tools or the Chromium depot_tools directory to PATH")
    env = os.environ.copy()
    env.setdefault("DEPOT_TOOLS_WIN_TOOLCHAIN", "0")
    env.setdefault("vs2022_install", r"D:\Microsoft\vs\2022\Community")
    env.setdefault("WINDOWSSDKDIR", r"D:\Windows Kits\10")
    env["PATH"] = str(depot_tools) + os.pathsep + env.get("PATH", "")
    command = [autoninja, "-C", str(build_dir), target]
    subprocess.run(command, cwd=chromium_root / "src", env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description="Extract V8 interface templates from Chromium-generated Blink bindings.")
    parser.add_argument("paths", nargs="+", type=Path,
                        help="output path; legacy form also accepts <bindings-dir> <output>")
    parser.add_argument("--chromium-root", type=Path, default=DEFAULT_CHROMIUM_ROOT,
                        help="Chromium checkout root (default: %(default)s)")
    parser.add_argument("--build-dir", type=Path, default=None,
                        help="GN output directory (default: <chromium-root>/src/out/Cronet145)")
    parser.add_argument("--bindings-dir", type=Path, default=None,
                        help="existing generated bindings directory; skips building")
    parser.add_argument("--target", default="blink_generate_bindings_all",
                        help="Ninja target used to generate Blink V8 bindings")
    parser.add_argument("--no-build", action="store_true",
                        help="fail if generated bindings are absent instead of invoking autoninja")
    parser.add_argument("--reviews", type=Path, default=Path(__file__).with_name("webidl-reviews.json"))
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if len(args.paths) not in (1, 2):
        parser.error("provide <output> or the legacy <bindings-dir> <output> form")
    build_dir = args.build_dir or (args.chromium_root / "src" / "out" / "Cronet145")
    legacy_renderer = args.paths[0] if len(args.paths) == 2 else None
    output_path = args.paths[-1]
    renderer = args.bindings_dir or legacy_renderer or generated_bindings_dir(build_dir)
    if not any(renderer.rglob("v8_*.cc")):
        if args.no_build:
            parser.error(f"generated Blink bindings not found under {renderer}")
        build_bindings(args.chromium_root, build_dir, args.target)
        renderer = generated_bindings_dir(build_dir)
    if not any(renderer.rglob("v8_*.cc")):
        parser.error(f"binding generation completed but no v8_*.cc files were found under {renderer}")
    extracted = extract(renderer, args.chromium_root / "src" / "third_party" / "blink" / "renderer")
    if args.reviews:
        apply_reviews(extracted, args.reviews)
    report = extracted.pop("_report")
    output = {"_webidl": {"schema": 3, "source": "tools/extract_interfaces.py", "renderer_snapshot": str(renderer),
        "signature_source": "Chromium-generated binding callbacks", "unresolved_policy": "implemented-strict-stub-permissive",
        "notes": "All generated core/modules binding tables are scanned; generation-only reviews are embedded into this snapshot.", "report": report}, **extracted}
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report: print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__": main()
