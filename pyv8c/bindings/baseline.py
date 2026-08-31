"""Chromium interface baseline loading with a two-level disk cache.

The repository reads immutable baseline snapshots (schema 5, compact) and
shares them between Browsers when the underlying file bytes are identical.
A binary pickle cache next to the source skips JSON parsing and validation
on startup; any corruption silently falls back to the JSON path so startup
can never fail because of a broken cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import pickle
import time
import zipfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = PACKAGE_ROOT / "baselines" / "interface-baseline.zip"


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


# Binary cache header. Bump the version whenever the cached payload shape
# changes; anything unreadable is simply rebuilt from the JSON baseline.
_BINARY_CACHE_MAGIC = b"pyv8c-baseline/1"
_BINARY_CACHE_SUFFIX = ".pyv8ccache"


class BaselineRepository:
    """Read immutable baseline snapshots, sharing only identical file contents."""

    _cache: dict[tuple[str, str], BaselineSnapshot] = {}
    _stat_cache: dict[tuple[str, int, int], BaselineSnapshot] = {}
    _last_timings: dict[str, float] = {}

    @staticmethod
    def _read_binary_cache(path, digest):
        """Return the cached ``(metadata, interfaces)`` pair, or ``None``.

        A cache is only usable when it was produced from byte-identical source
        and declares the supported schema. Any corruption or I/O problem makes
        it fall back to the JSON path rather than failing startup.
        """
        try:
            with open(path, "rb") as handle:
                if handle.read(len(_BINARY_CACHE_MAGIC)) != _BINARY_CACHE_MAGIC:
                    return None
                header = pickle.load(handle)
                if not isinstance(header, dict) or header.get("digest") != digest:
                    return None
                if header.get("schema") != 5:
                    return None
                payload = pickle.load(handle)
        except Exception:
            return None
        if not isinstance(payload, tuple) or len(payload) != 2:
            return None
        metadata, interfaces = payload
        if not isinstance(metadata, dict) or not isinstance(interfaces, dict):
            return None
        return metadata, interfaces

    @staticmethod
    def _write_binary_cache(path, digest, metadata, interfaces):
        """Best-effort cache write; startup must never depend on it."""
        try:
            with open(path, "wb") as handle:
                handle.write(_BINARY_CACHE_MAGIC)
                pickle.dump({"digest": digest, "schema": 5}, handle,
                            protocol=pickle.HIGHEST_PROTOCOL)
                pickle.dump((metadata, interfaces), handle,
                            protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            return False
        return True

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
        binary_cache = target.with_suffix(_BINARY_CACHE_SUFFIX)
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
        except UnicodeDecodeError as exc:
            raise ValueError(f"Chromium interface baseline must be UTF-8 JSON: {target}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Chromium interface baseline JSON: {target}") from exc
        digest = sha256(raw).hexdigest()

        cached_payload = (BaselineRepository._read_binary_cache(binary_cache, digest)
                          if binary_cache.is_file() else None)
        if cached_payload is not None:
            # Skip JSON parsing, the interface projection and the cycle walk:
            # the cache is only valid for byte-identical source that already
            # passed every check.
            metadata, interfaces = cached_payload
            parsed = time.perf_counter_ns()
        else:
            try:
                document = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise ValueError(f"Chromium interface baseline must be UTF-8 JSON: {target}") from exc
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Chromium interface baseline JSON: {target}") from exc
            parsed = time.perf_counter_ns()
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
            BaselineRepository._write_binary_cache(
                binary_cache, digest, metadata, interfaces,
            )
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
            "baseline_binary_cache": cached_payload is not None,
        }
        return snapshot
