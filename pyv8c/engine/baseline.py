"""The interface baseline: its bytes, its shape, and the lookups derived from them.

One snapshot is immutable and shared between Browsers whose file bytes match.  This
module reads and writes it, decodes each interface into what a realm asks about it
(parent chain, members, interface object, exposure) and plans one realm from those
answers.  Nothing here knows the native binding, which keeps it importable by the
offline extractor.
"""

from __future__ import annotations

import json
import pickle
import time
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = PACKAGE_ROOT / "baselines" / "interface-baseline.zip"
#: The profile a collection run writes; the surface is a section of it.
PROFILE_PATH = PACKAGE_ROOT / "data" / "chrome-environment.json"


@dataclass(frozen=True)
class WindowSurface:
    """The own property names of a reference ``window``, and the browser they came from.

    The baseline carries every platform and runtime flag state Chromium compiles in; the
    reference browser installs a subset, which ``tools/collect_environment.py`` reads from
    a running Chrome, so the difference stays data instead of a hand-written list.
    """

    chrome: str
    own_names: frozenset

    def has(self, name) -> bool:
        return name in self.own_names


def load_window_surface(path=None) -> WindowSurface | None:
    """The surface the reference browser reported, or ``None``.

    It is the ``window_surface`` section of the profile a collection run writes.
    """
    target = Path(path) if path is not None else PROFILE_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    section = payload.get("window_surface") if isinstance(payload, Mapping) else None
    names = (section or {}).get("own_names")
    if not isinstance(names, list) or not names:
        return None
    return WindowSurface(
        chrome=str((payload.get("source") or {}).get("browser") or ""),
        own_names=frozenset(str(name) for name in names),
    )


def write_baseline(output_path: Path, document: dict, *, compress: bool = False) -> Path:
    """Write a compact schema 5 baseline as JSON or a single-member ZIP."""
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output_path = Path(output_path)
    if not (compress or output_path.suffix.lower() == ".zip"):
        output_path.write_bytes(payload)
        return output_path
    archive_path = output_path if output_path.suffix.lower() == ".zip" else output_path.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(archive_path.with_suffix(".json").name, payload)
    return archive_path


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


# Bump the magic whenever the cached payload shape changes; unreadable caches are rebuilt.
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

        Usable only when produced from byte-identical source and the supported schema;
        any corruption or I/O problem falls back to the JSON path.
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
        except Exception:  # a corrupt cache simply reads as "not cached"
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
        except Exception:  # a failed write is reported by the return value
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
            # Skip JSON parsing, projection and cycle walk: the cache is only valid for byte-identical source.
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
            # Repository-owned and never mutated; a top-level read-only view avoids copying the 7.5 MB baseline.
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


LOCATIONS = ("instance", "prototype", "interface")


@dataclass(frozen=True)
class Member:
    """One declared member, as the snapshot states it.

    ``flags``, ``receiver``, ``cross_origin`` and ``side_effect`` keep the shorthand the
    snapshot writes; what they stand for is decided where the spec is built.
    ``main_world`` is the one world fact a runtime snapshot keeps.
    """

    name: str
    kind: str
    location: str
    flags: str
    property_flags: int | None
    receiver: str
    cross_origin: str
    side_effect: str
    length: int | None
    value: int | None
    #: ``True``/``False`` when the baseline states the setter, ``None`` when it is silent.
    setter_state: bool | None
    main_world: bool

    @property
    def is_static(self) -> bool:
        return self.location == "interface"


def _member(location, entry) -> Member:
    length = entry.get("length")
    stated_flags = entry.get("property_flags")
    return Member(
        name=str(entry.get("name") or ""),
        kind=str(entry.get("kind") or ""),
        location=location,
        flags=str(entry.get("flags") or ""),
        property_flags=stated_flags if isinstance(stated_flags, int) else None,
        receiver=str(entry.get("receiver") or ""),
        cross_origin=str(entry.get("cross_origin") or ""),
        side_effect=str(entry.get("side_effect") or ""),
        length=int(length) if isinstance(length, int) else None,
        value=(entry.get("value") if isinstance(entry.get("value"), int) else None),
        setter_state=(bool(entry["setter"]) if "setter" in entry else None),
        # A snapshot that predates the bit states no world name at all, which is itself
        # the main world: the omission meant exactly that.
        main_world=bool(entry.get("main_world", True)),
    )


@dataclass(frozen=True)
class Interface:
    """One baseline entry, with what the runtime asks for computed once."""

    name: str
    parent: str | None
    tag: str
    exposed_in: frozenset
    has_interface_object: bool
    template: Mapping
    members: Mapping
    constants: tuple
    #: The constructor's declared shape; ``constructor_stated`` is False when the
    #: baseline said nothing, which leaves the hand-written spec in charge.
    constructor_length: int | None
    constructor_new: bool
    constructor_call: bool
    constructor_stated: bool
    raw: Mapping

    def own_members(self, location) -> tuple:
        return self.members.get(location, ())


class InterfaceTable:
    """Read-only indexes over one immutable baseline snapshot."""

    def __init__(self, baseline, *, surface=None):
        self.baseline = baseline
        self.surface = surface
        self._interfaces: dict[str, Interface] = {}
        self._positions: dict[str, int] | None = None
        self._children: dict[str, tuple] | None = None
        self._descendants: dict[str, tuple] = {}
        self._ancestors: dict[str, tuple] = {}
        self._members: dict[tuple, Member | None] = {}
        self._exposed: dict[tuple, tuple] = {}
        self._aliases: dict[str, str] | None = None

    def names(self) -> tuple:
        """Every interface name, in baseline order."""
        return tuple(self.baseline.interfaces)

    def raw(self, name):
        return self.baseline.interfaces[name]

    def interface(self, name) -> Interface:
        cached = self._interfaces.get(name)
        if cached is None:
            cached = self._build(name, self.baseline.interfaces[name])
            self._interfaces[name] = cached
        return cached

    @staticmethod
    def _build(name, entry) -> Interface:
        members = entry.get("members") or {}
        constructor = entry.get("constructor") or {}
        length = constructor.get("length")
        return Interface(
            name=name,
            parent=entry.get("parent"),
            tag=str(entry.get("tag") or ""),
            exposed_in=frozenset(entry.get("exposed_in") or ()),
            has_interface_object=bool(constructor.get("exposed")),
            template=entry.get("template") or {},
            members=MappingProxyType({
                location: tuple(_member(location, item) for item in (members.get(location) or ()))
                for location in LOCATIONS
            }),
            constants=tuple(entry.get("constants") or ()),
            constructor_length=int(length) if isinstance(length, int) else None,
            constructor_new=bool(constructor.get("allow_new")),
            constructor_call=bool(constructor.get("allow_call")),
            constructor_stated=("allow_new" in constructor or "allow_call" in constructor),
            raw=entry,
        )

    def ancestors(self, name) -> tuple:
        """The interface and its ancestors, innermost first."""
        cached = self._ancestors.get(name)
        if cached is None:
            chain, current, seen = [], name, set()
            while current and current not in seen:
                seen.add(current)
                chain.append(current)
                current = self.baseline.interfaces[current].get("parent")
            cached = tuple(chain)
            self._ancestors[name] = cached
        return cached

    def children(self, name) -> tuple:
        if self._children is None:
            index = {}
            for candidate, entry in self.baseline.interfaces.items():
                parent = entry.get("parent")
                if parent:
                    index.setdefault(parent, []).append(candidate)
            self._children = {key: tuple(value) for key, value in index.items()}
        return self._children.get(name, ())

    def descendants(self, name) -> tuple:
        cached = self._descendants.get(name)
        if cached is None:
            seen, pending = set(), list(self.children(name))
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(self.children(current))
            positions = self.positions()
            cached = tuple(sorted(seen, key=positions.__getitem__))
            self._descendants[name] = cached
        return cached

    def positions(self) -> dict:
        if self._positions is None:
            self._positions = {name: index
                               for index, name in enumerate(self.baseline.interfaces)}
        return self._positions

    def own_members(self, name) -> tuple:
        """``(location, member)`` pairs declared directly on one interface."""
        interface = self.interface(name)
        members = tuple(
            (location, member)
            for location in LOCATIONS
            for member in interface.own_members(location)
        )
        if name != "Window" or self.surface is None:
            return members
        # The baseline carries every platform and flag state; the reference browser does not.
        return tuple(pair for pair in members if self.surface.has(pair[1].name))

    def member(self, name, public_name, kind) -> Member | None:
        """The member an interface answers for a public name, inherited or not."""
        key = (name, public_name, kind)
        if key in self._members:
            return self._members[key]
        found = None
        for current in self.ancestors(name):
            match = self._own_member(current, public_name, kind)
            if match is not None:
                found = match
                break
        if found is None:
            # Blink sometimes installs WorkerGlobalScope members in a concrete descendant.
            for candidate in self.descendants(name):
                found = self._own_member(candidate, public_name, kind)
                if found is not None:
                    break
        self._members[key] = found
        return found

    def _own_member(self, name, public_name, kind) -> Member | None:
        candidates = [member for _location, member in self.own_members(name)
                      if member.name == public_name and member.kind == kind]
        if not candidates:
            return None
        candidates.sort(key=lambda member: not member.main_world)
        return candidates[0]

    def installs(self, context_type, name) -> bool:
        """Whether the reference browser installs this Window name at all."""
        return self.surface is None or context_type != "Window" or self.surface.has(name)

    def window_aliases(self) -> dict:
        """``alias -> interface`` for the Window aliases the reference browser installs."""
        cached = self._aliases
        if cached is None:
            exposed = set(self.exposed("Window"))
            cached = {
                alias: name
                for name, entry in self.baseline.interfaces.items()
                if name in exposed
                for alias in (entry.get("aliases") or ())
                if self.installs("Window", alias) and alias not in exposed
            }
            self._aliases = cached
        return cached

    def exposed(self, context_type) -> tuple:
        """Names the baseline exposes for *context_type*, in baseline order."""
        cached = self._exposed.get(context_type)
        if cached is None:
            cached = tuple(
                name for name, entry in self.baseline.interfaces.items()
                if context_type in (entry.get("exposed_in") or ())
                and self.installs(context_type, name)
            )
            self._exposed[context_type] = cached
        return cached


@dataclass(frozen=True)
class RealmPlan:
    """The exposure plan of one realm class."""

    context_type: str
    #: Installed on the global, built on first read.
    declared: tuple[str, ...]
    #: The skeleton the realm stages before its first script runs.
    staged: tuple[str, ...]
    #: Built once the realm is ready: the skeleton, its ancestors and the alias
    #: targets.  The only answer to "what is built before the first script runs".
    startup: tuple[str, ...] = ()

    @property
    def deferred(self) -> tuple[str, ...]:
        staged = set(self.staged)
        return tuple(name for name in self.declared if name not in staged)


def build_plan(table, context_type, *, implements=(), expose_unimplemented=True, staged=()) -> RealmPlan:
    """Plan one realm class from the table.

    The names a realm declares and the skeleton it stages are one object, so a realm and
    its statistics read the same plan.
    """
    declared = tuple(sorted(
        name for name in table.exposed(context_type)
        if expose_unimplemented or name in implements
    ))
    return RealmPlan(
        context_type=context_type,
        declared=declared,
        staged=tuple(name for name in declared if name in set(staged) or name == "Window"),
    )
