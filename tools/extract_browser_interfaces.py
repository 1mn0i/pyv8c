"""Generate a schema-3 interface baseline from a visible Chrome/Edge process.

The collector deliberately uses only JavaScript reflection.  It never invokes a
constructor, getter, operation, or other unknown browser code.  CDP is used
directly so the tool stays usable without Playwright or Selenium.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import urlopen

# Source checkouts often keep the native pyv8 extension in a sibling `_pyv8`
# directory rather than in the active interpreter's site-packages.
_LOCAL_PYV8 = Path(__file__).resolve().parents[2] / "_pyv8"
if _LOCAL_PYV8.is_dir() and str(_LOCAL_PYV8) not in sys.path:
    sys.path.insert(0, str(_LOCAL_PYV8))
_LOCAL_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_LOCAL_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_LOCAL_PACKAGE_ROOT))


try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:  # pragma: no cover - exercised by the CLI dependency check
    websocket_connect = None


_BLACKLIST = {
    "Object", "Function", "Array", "Number", "parseFloat", "parseInt", "Date",
    "String", "RegExp", "Error", "EvalError", "RangeError", "ReferenceError",
    "SyntaxError", "TypeError", "URIError", "AggregateError", "Boolean", "Symbol",
    "BigInt", "Math", "JSON", "Reflect", "Proxy", "Map", "Set", "WeakMap",
    "WeakSet", "WeakRef", "FinalizationRegistry", "ArrayBuffer", "DataView",
    "Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array",
    "Int32Array", "Uint32Array", "Float32Array", "Float64Array", "BigInt64Array",
    "BigUint64Array", "Atomics", "JSON", "WebAssembly", "Promise", "Generator",
    "GeneratorFunction", "AsyncFunction", "AsyncGenerator", "Intl", "WebSocketPair",
}

_MEMBER_FUNCTION_KEYS = {"length", "name", "arguments", "caller", "prototype"}
_ANY_SIGNATURE = {
    "parameters": [{"default": None, "idl_type": "any", "index": 0,
                     "nullable": True, "optional": True, "review": "manual-reviewed"}],
    "required": 0,
    "source_evidence": "manual-reviewed runtime fallback",
    "status": "manual-reviewed",
}


REFLECT_GLOBAL_JS = r"""
(() => {
  const blacklist = new Set(%s);
  const memberFunctionKeys = new Set(["length", "name", "arguments", "caller", "prototype"]);
  const ownKeys = value => Reflect.ownKeys(value).filter(key => typeof key === "string");
  const safeDescriptor = (value, key) => {
    try { return Object.getOwnPropertyDescriptor(value, key); } catch (_) { return null; }
  };
  const safeFunction = value => {
    try { return typeof value === "function" ? value : null; } catch (_) { return null; }
  };
  const isCandidate = name => typeof name === "string" && /^[A-Z][A-Za-z0-9]*$/.test(name)
    && !blacklist.has(name);
  const constructible = value => {
    try { Reflect.construct(String, [], value); return true; } catch (_) { return false; }
  };
  const member = (name, descriptor, location) => {
    const value = descriptor && "value" in descriptor ? descriptor.value : undefined;
    const fn = safeFunction(value);
    if (fn) return {
      name, kind: "operation", location, callback: `BrowserReflection_${name}OperationCallback`,
      length: Number.isFinite(fn.length) ? fn.length : null, tag: null,
      flags: "v8::None", receiver: "Check", cross_origin: "Check",
      side_effect: "HasSideEffect", world: "AllWorlds", signature: {
        parameters: [], required: 0, source_evidence: "browser reflection cannot expose Web IDL types",
        status: "unresolved"
      }
    };
    if (descriptor && ("get" in descriptor || "set" in descriptor)) return {
      name, kind: "attribute", location, callback: `BrowserReflection_${name}AttributeCallback`,
      length: null, tag: null, flags: descriptor.set ? "v8::None" : "v8::DontWrite",
      readonly: !descriptor.set, receiver: "Check", cross_origin: "Check",
      side_effect: "HasSideEffect", world: "AllWorlds",
      getter: Boolean(descriptor.get), setter: Boolean(descriptor.set),
      signature: {parameters: [], source_evidence: "attribute getter has no Web IDL parameters",
        status: "unresolved"},
      setter_signature: descriptor.set ? {
        parameters: [{default: null, idl_type: "unresolved", index: 0, nullable: false,
          optional: false, review: "browser-reflection"}], required: 1,
        source_evidence: "browser reflection cannot expose Web IDL types", status: "unresolved"
      } : null
    };
    if (descriptor && "value" in descriptor && descriptor.value !== undefined)
      return {name, kind: "constant", location, callback: null, length: null, tag: null,
        flags: "v8::None", receiver: "DoNotCheck", cross_origin: "DoNotCheck",
        side_effect: "HasNoSideEffect", world: "AllWorlds"};
    return null;
  };
  const collect = (name, value) => {
    let proto = null, tag = name;
    try { proto = value.prototype; } catch (_) {}
    try { const d = Object.getOwnPropertyDescriptor(value, Symbol.toStringTag); if (d && typeof d.value === "string") tag = d.value; } catch (_) {}
    const result = {name, kind: "interface", parent: null, constructible: constructible(value),
      constructor: {allow_call: null, allow_new: constructible(value), exposed: true,
        length: Number.isFinite(value.length) ? value.length : null, status: "reflected",
        signature: {parameters: [], required: 0, source_evidence: "browser reflection cannot expose Web IDL constructor types", status: "unresolved"}},
      exposed_in: ["Window"], length: Number.isFinite(value.length) ? value.length : null,
      tag, members: {instance: [], interface: [], prototype: []}, mixins: [],
      parent: null, source: "browser-reflection", sources: ["browser-reflection"], snapshot: "browser-cdp"};
    if (proto) for (const key of ownKeys(proto)) {
      if (memberFunctionKeys.has(key) || key === "constructor") continue;
      const d = safeDescriptor(proto, key), item = member(key, d, "prototype");
      if (item) result.members.prototype.push(item);
    }
    for (const key of ownKeys(value)) {
      if (memberFunctionKeys.has(key)) continue;
      const d = safeDescriptor(value, key), item = member(key, d, "interface");
      if (item) result.members.interface.push(item);
    }
    if (name === "Window") {
      for (const key of ownKeys(globalThis)) {
        if (memberFunctionKeys.has(key) || isCandidate(key)) continue;
        const d = safeDescriptor(globalThis, key), item = member(key, d, "instance");
        if (item) result.members.instance.push(item);
      }
    }
    try {
      const p = Object.getPrototypeOf(proto);
      if (p && p.constructor && typeof p.constructor.name === "string" && p.constructor.name !== "Object" && !blacklist.has(p.constructor.name)) result.parent = p.constructor.name;
    } catch (_) {}
    return result;
  };
  const result = [];
  for (const name of ownKeys(globalThis)) {
    if (!isCandidate(name)) continue;
    const descriptor = safeDescriptor(globalThis, name);
    let value;
    try { value = descriptor && "value" in descriptor ? descriptor.value : globalThis[name]; } catch (_) { value = null; }
    if (typeof value !== "function") continue;
    try { result.push(collect(name, value)); } catch (_) {}
  }
  return result;
})()
""" % json.dumps(sorted(_BLACKLIST))


def _http_json(url: str, timeout: float = 0.5) -> dict[str, Any] | list[Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CDPConnection:
    def __init__(self, websocket_url: str, timeout: float = 10.0):
        if websocket_connect is None:
            raise RuntimeError("browser collection requires the 'websockets' package; install pyv8c[browser]")
        self.websocket = websocket_connect(websocket_url, open_timeout=timeout, max_size=None)
        self.timeout = timeout
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request = {"id": self._next_id, "method": method, "params": params or {}}
        self.websocket.send(json.dumps(request))
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            message = json.loads(self.websocket.recv(timeout=remaining))
            if message.get("id") != request["id"]:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
            "userGesture": True, "replMode": True,
        })
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise RuntimeError(detail.get("text") or "browser reflection script failed")
        value = result.get("result", {}).get("value")
        if value is None and result.get("result", {}).get("subtype") == "error":
            raise RuntimeError("browser reflection returned an error")
        return value

    def close(self) -> None:
        try:
            self.websocket.close()
        except Exception:
            pass


class VisibleBrowser:
    """Owns a visible browser process and its temporary profile."""

    def __init__(self, executable: str, *, timeout: float = 20.0):
        self.executable = str(Path(executable).expanduser().resolve())
        self.timeout = timeout
        self.profile = Path(tempfile.mkdtemp(prefix="pyv8c-cdp-"))
        self.port = _free_port()
        self.process: subprocess.Popen | None = None
        self.cdp: CDPConnection | None = None
        self.version: dict[str, Any] = {}

    def __enter__(self) -> "VisibleBrowser":
        args = browser_args(self.executable, self.port, self.profile)
        # Deliberately no --headless: collection is visible to the user.
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + self.timeout
        version_url = f"http://127.0.0.1:{self.port}/json/version"
        while time.monotonic() < deadline:
            try:
                self.version = _http_json(version_url, timeout=0.5)  # type: ignore[assignment]
                break
            except (OSError, URLError, ValueError):
                if self.process.poll() is not None:
                    raise RuntimeError(f"browser exited with status {self.process.returncode}")
                time.sleep(0.1)
        else:
            raise TimeoutError(f"timed out waiting for CDP endpoint {version_url}")
        targets = _http_json(f"http://127.0.0.1:{self.port}/json/list")
        page = next((target for target in targets if target.get("type") == "page"), None)
        if not page or not page.get("webSocketDebuggerUrl"):
            raise RuntimeError("browser did not expose a page target")
        self.cdp = CDPConnection(page["webSocketDebuggerUrl"], timeout=self.timeout)
        self.cdp.call("Runtime.enable")
        return self

    def collect(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.cdp is None:
            raise RuntimeError("browser is not connected")
        window = self.cdp.evaluate(REFLECT_GLOBAL_JS)
        worker_expression = """
        (() => new Promise((resolve, reject) => {
          const source = %s;
          const blob = new Blob([`self.onmessage=()=>{try{postMessage(${source})}catch(e){postMessage({__error:String(e)})}}`], {type:'text/javascript'});
          const objectUrl = URL.createObjectURL(blob);
          const worker = new Worker(objectUrl);
          worker.onmessage = event => { worker.terminate(); URL.revokeObjectURL(objectUrl); resolve(event.data); };
          worker.onerror = event => { worker.terminate(); URL.revokeObjectURL(objectUrl); reject(new Error(event.message)); };
          worker.postMessage(0);
        }))()
        """ % REFLECT_GLOBAL_JS[REFLECT_GLOBAL_JS.find("(() =>"):REFLECT_GLOBAL_JS.rfind("\n")]
        worker = self.cdp.evaluate(worker_expression)
        if isinstance(worker, dict) and worker.get("__error"):
            raise RuntimeError(worker["__error"])
        for item in window or []:
            item["exposed_in"] = ["Window"]
        for item in worker or []:
            item["exposed_in"] = ["Worker"]
        return window or [], worker or []

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.cdp:
            self.cdp.close()
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try: self.process.kill()
                except Exception: pass
        shutil.rmtree(self.profile, ignore_errors=True)


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser_args(executable: str, port: int, profile: Path) -> list[str]:
    """Return launch arguments; visibility is intentional and testable."""
    return [str(executable), f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-popup-blocking", "--new-window",
            "about:blank"]


def _merge_environments(window: Iterable[dict[str, Any]], worker: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in [*window, *worker]:
        name = item["name"]
        if name not in merged:
            merged[name] = deepcopy(item)
            continue
        target = merged[name]
        target["exposed_in"] = sorted(set(target.get("exposed_in", [])) | set(item.get("exposed_in", [])))
        if not target.get("parent") and item.get("parent"):
            target["parent"] = item["parent"]
        for location in ("instance", "prototype", "interface"):
            known = {(m.get("kind"), m.get("name")) for m in target["members"].get(location, [])}
            target["members"][location].extend(
                deepcopy(member) for member in item.get("members", {}).get(location, [])
                if (member.get("kind"), member.get("name")) not in known
            )
    return merged


def _signature_table(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"constructors": {}, "members": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {"constructors": {}, "members": {}}


def _load_reference(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in value.items() if not k.startswith("_")} if isinstance(value, dict) else {}


def _generic_signature(required: int = 0) -> dict[str, Any]:
    signature = deepcopy(_ANY_SIGNATURE)
    signature["parameters"] = [dict(signature["parameters"][0], index=i, optional=False)
                                for i in range(required)]
    signature["required"] = required
    return signature


def _apply_signatures(interfaces: dict[str, dict[str, Any]], table: dict[str, Any], reference: dict[str, Any]) -> None:
    constructors = table.get("constructors", {}) if isinstance(table, dict) else {}
    members = table.get("members", {}) if isinstance(table, dict) else {}
    for name, spec in interfaces.items():
        ref = reference.get(name, {})
        constructor = spec.setdefault("constructor", {})
        if name in constructors:
            constructor["signature"] = deepcopy(constructors[name])
            constructor["status"] = constructor["signature"].get("status", "unresolved")
        elif (ref.get("constructor") or {}).get("signature"):
            constructor["signature"] = deepcopy(ref["constructor"]["signature"])
            constructor["status"] = constructor["signature"].get("status", "unresolved")
        elif constructor.get("constructible") or spec.get("constructible"):
            constructor["signature"] = _generic_signature(int(spec.get("length") or 0))
            constructor["status"] = "manual-reviewed"
        for location, values in spec.get("members", {}).items():
            for member in values:
                key = f"{name}.{member.get('name')}"
                patch = members.get(key)
                ref_member = next((m for group in (ref.get("members") or {}).values() for m in group
                                   if m.get("name") == member.get("name") and m.get("kind") == member.get("kind")), None)
                if member.get("kind") == "operation":
                    if patch:
                        member["signature"] = deepcopy(patch)
                    elif ref_member and ref_member.get("signature"):
                        member["signature"] = deepcopy(ref_member["signature"])
                    else:
                        member["signature"] = _generic_signature(int(member.get("length") or 0))
                elif member.get("kind") == "attribute" and member.get("setter"):
                    if patch and patch.get("setter_signature"):
                        member["setter_signature"] = deepcopy(patch["setter_signature"])
                    elif ref_member and ref_member.get("setter_signature"):
                        member["setter_signature"] = deepcopy(ref_member["setter_signature"])
                    else:
                        member["setter_signature"] = _generic_signature(1)


def _merge_reference_members(interfaces: dict[str, dict[str, Any]], reference: dict[str, Any]) -> None:
    """Preserve runtime-declared members absent from exotic host objects.

    Objects such as ``Location`` expose their Web IDL members on an instance
    rather than on the public constructor prototype, so constructor reflection
    alone cannot discover them.
    """
    try:
        from pyv8c.interfaces import IMPLEMENTATIONS
    except Exception:
        return
    for name in IMPLEMENTATIONS:
        target, source = interfaces.get(name), reference.get(name)
        if not target or not source:
            continue
        for location, values in (source.get("members") or {}).items():
            existing = {(item.get("name"), item.get("kind")) for item in target["members"].get(location, [])}
            for item in values:
                key = (item.get("name"), item.get("kind"))
                if key not in existing:
                    target["members"].setdefault(location, []).append(deepcopy(item))


def _runtime_interfaces(interfaces: dict[str, dict[str, Any]], reference: dict[str, Any]) -> None:
    """Keep registered pyv8c implementations available if a browser hides one."""
    try:
        from pyv8c.interfaces import IMPLEMENTATIONS, INTERFACES
    except Exception:
        return
    for name, spec in INTERFACES.items():
        if name in interfaces:
            continue
        if name in reference:
            interfaces[name] = deepcopy(reference[name])
            continue
        members = {"instance": [], "prototype": [], "interface": []}
        cls = IMPLEMENTATIONS.get(name)
        for config_name, kind, location in (("__v8_attribute__", "attribute", "prototype"),
                                             ("__v8_method__", "operation", "prototype")):
            for config in getattr(cls, config_name, ()) or ():
                members[location].append({"name": config[0], "kind": kind, "location": location,
                    "callback": f"BrowserReflection_{config[0]}Callback", "length": config[2] if kind == "operation" else None,
                    "flags": "v8::None", "receiver": "Check", "cross_origin": "Check",
                    "side_effect": "HasSideEffect", "world": "AllWorlds", "signature": {"parameters": [], "status": "unresolved"}})
        interfaces[name] = {"name": name, "kind": "interface", "parent": spec.parent,
            "constructible": spec.constructible, "constructor": {"allow_call": None, "allow_new": spec.constructible,
                "exposed": True, "length": spec.length, "status": "manual-reviewed", "signature": _generic_signature(spec.length)},
            "exposed_in": list(spec.exposed_in), "length": spec.length, "tag": spec.tag,
            "members": members, "mixins": list(spec.mixins), "source": "browser-reflection-fallback",
            "sources": ["browser-reflection-fallback"], "snapshot": "browser-cdp"}


def build_baseline(window: list[dict[str, Any]], worker: list[dict[str, Any]], *, browser: dict[str, Any],
                   signature_path: Path | None = None, reference_path: Path | None = None) -> dict[str, Any]:
    interfaces = _merge_environments(window, worker)
    reference = _load_reference(reference_path)
    _runtime_interfaces(interfaces, reference)
    try:
        from pyv8c.interfaces import INTERFACES
        for name, registered in INTERFACES.items():
            if name in interfaces and registered.parent:
                interfaces[name]["parent"] = registered.parent
    except Exception:
        pass
    for spec in interfaces.values():
        if spec.get("parent") not in interfaces:
            spec["parent"] = None
    _merge_reference_members(interfaces, reference)
    _apply_signatures(interfaces, _signature_table(signature_path) if signature_path else {}, reference)
    browser_string = str(browser.get("Browser") or browser.get("product") or "Unknown/unknown")
    match = re.match(r"([^/]+)/(.+)", browser_string)
    metadata = {"schema": 3, "source": "tools/extract_browser_interfaces.py", "generator": "browser-cdp",
                "signature_source": str(signature_path) if signature_path else None,
                "browser": {"name": match.group(1) if match else browser_string, "version": match.group(2) if match else None,
                             "product": browser_string, "platform": platform.platform()},
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "unresolved_policy": "implemented-strict-stub-permissive",
                "notes": "Visible browser CDP reflection; Web IDL parameter types unavailable from JavaScript are unresolved.",
                "report": {"interfaces": len(interfaces), "source": "browser-reflection"}}
    return {**interfaces, "_webidl": metadata}


def collect(executable: str, output: Path, *, signature_path: Path, reference_path: Path | None = None) -> dict[str, Any]:
    with VisibleBrowser(executable) as browser:
        window, worker = browser.collect()
        baseline = build_baseline(window, worker, browser=browser.version,
                                  signature_path=signature_path, reference_path=reference_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, help="Chrome/Edge executable path")
    parser.add_argument("--output", required=True, type=Path, help="schema 3 JSON output path")
    parser.add_argument("--signatures", type=Path, default=Path(__file__).with_name("runtime-signatures.json"))
    parser.add_argument("--reference-baseline", type=Path,
                        default=Path(__file__).parents[1] / "pyv8c" / "baselines" / "interface-baseline.json")
    args = parser.parse_args(argv)
    try:
        baseline = collect(args.browser, args.output, signature_path=args.signatures,
                           reference_path=args.reference_baseline)
    except KeyboardInterrupt:
        print("browser interface collection interrupted; browser/profile cleaned up")
        return 130
    except Exception as exc:
        parser.error(str(exc))
        return 2
    print(f"wrote {len(baseline) - 1} interfaces to {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
