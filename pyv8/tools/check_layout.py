#!/usr/bin/env python3
"""Check that includes follow `docs/pyv8/source-layout.md`.

The binding is arranged so that a file may use what is below it and nothing
above, in this order: the shims, the isolate, the values, the handles and the
tracing beside them, then the interfaces.  A handful of files exist to tie those
layers together and are allowed to reach upward; they are listed in
`COMPOSITION_EDGES` below and each one is explained in the layout document.

The default is report mode: it prints what the tree looks like and always exits
0, which is what makes it usable while the tree is being moved.  `--enforce`
exits 1 when anything is out of place, which is how the layout stops drifting
afterwards.

`--list` prints the files in each group and is the check that a move put every
file where the document says it goes.

It also holds the two build files to the same tree: `setup.py` has to hand the
compiler every group as an include root, because the sources include their
neighbours by bare name, and `pyproject.toml` has to name package data that is
actually there.  A group that exists in the tree and not in the build is a group
whose files cannot see each other.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = PROJECT_ROOT / "pyv8"

#: Package data the build produces rather than the tree holding it, so its absence
#: from a fresh checkout says nothing.
GENERATED_PACKAGE_DATA = {"icudtl.dat", "_pyv8.pyd", "_pyv8.abi3.so"}

#: Directory under `src/` -> layer.  A file may include headers from its own
#: layer and from lower ones.  A directory that is not listed here is reported:
#: a new group is a decision to write into the layout document, not a thing to
#: discover later.
LAYERS = {
    ".": 0,           # shims and the module entry point
    "isolate": 1,     # the isolate, the realm and their lifetimes
    "values": 2,      # values crossing the boundary, and errors
    "handles": 3,     # the Python types that stand for JavaScript values
    "tools": 3,       # tracing, used by the interface layer, and the devtools bridge
    "interfaces": 4,  # declarations into templates into callbacks
}

#: Files whose whole purpose is to tie layers together, so an include that
#: reaches upward is what they are for.  Everything else follows the plain rule.
COMPOSITION_EDGES = {
    'isolate/context.cpp': frozenset({
        'handles/jsobject.h',
        'handles/script.h',
        'interfaces/idl_spec.h',
        'interfaces/pyclass.h',
        'interfaces/v8tools.h',
        'tools/debugger.h',
        'values/convert.h',
        'values/crossing.h',
        'values/jsvalue.h',
    }),
    'isolate/context_names.cpp': frozenset({
        'interfaces/idl_spec.h',
        'interfaces/pyclass.h',
        'interfaces/pyclass_cross_origin_handlers.h',
        'interfaces/v8tools.h',
        'tools/trace.h',
        'values/convert.h',
    }),
    'isolate/context_resources.cpp': frozenset({
        'handles/jsobject.h',
    }),
    'isolate/error_hooks.cpp': frozenset({
        'values/convert.h',
    }),
    'isolate/isolate_state.cpp': frozenset({
        'interfaces/pyclass.h',
        'tools/debugger.h',
        'values/error_family.h',
    }),
    'isolate/runtime.cpp': frozenset({
        'values/serialization.h',
    }),
    'isolate/wasm_streaming.cpp': frozenset({
        'values/convert.h',
        'values/error_family.h',
        'values/handle_resource.h',
    }),
    'pyv8.cpp': frozenset({
        'handles/jsbuffer.h',
        'handles/jsfunction.h',
        'handles/jsobject.h',
        'handles/jspromise.h',
        'handles/jstypedarray.h',
        'handles/script.h',
        'interfaces/idl_spec.h',
        'interfaces/pyclass.h',
        'interfaces/pyfunction.h',
        'isolate/context.h',
        'isolate/isolate.h',
        'isolate/watchdog.h',
        'tools/debugger.h',
        'values/convert.h',
        'values/error_family.h',
        'values/jsvalue.h',
        'values/serialization.h',
    }),
    'pyv8.h': frozenset({
        'isolate/entry.h',
        'isolate/execution.h',
        'isolate/isolate_state.h',
        'isolate/runtime.h',
        'values/crossing.h',
        'values/error_family.h',
        'values/js_exception.h',
        'values/jsnull.h',
        'values/jsundefined.h',
    }),
    'values/convert_to_javascript.cpp': frozenset({
        'handles/jsobject.h',
        'interfaces/pyclass.h',
        'interfaces/pyfunction.h',
    }),
    'values/convert_to_python.cpp': frozenset({
        'handles/jsobject.h',
    }),
    'values/error_family.cpp': frozenset({
        'tools/trace.h',
    }),
}

INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"')
SUFFIXES = (".cpp", ".h", ".def")


def group_of(path: Path) -> str:
    """The layout group a file belongs to."""
    relative = path.relative_to(SOURCE_ROOT)
    return "." if len(relative.parts) == 1 else relative.parts[0]


def source_files() -> list[Path]:
    return sorted(
        path for path in SOURCE_ROOT.rglob("*") if path.is_file() and path.suffix in SUFFIXES
    )


def includes_of(path: Path):
    """The quoted includes of a file, as (line number, text)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:  # pragma: no cover - unreadable source is fatal
        print(f"cannot read {path}: {error}", file=sys.stderr)
        raise SystemExit(2)
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = INCLUDE.match(line)
        if match:
            found.append((number, match.group(1)))
    return found


def include_roots_the_build_declares() -> list[str]:
    """The `include_dirs` setup.py hands the compiler, as written there."""
    text = (PROJECT_ROOT / "setup.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != 'Extension':
            continue
        for keyword in node.keywords:
            if keyword.arg == 'include_dirs':
                roots = ast.literal_eval(keyword.value)
                if not isinstance(roots, (list, tuple)) or not all(isinstance(root, str) for root in roots):
                    raise ValueError('Extension.include_dirs must be a literal list/tuple of paths')
                return list(roots)
    return []


def resolve_include(source: Path, included: str, roots: list[Path]) -> Path | None:
    relative = Path(included.replace('\\', '/'))
    for root in [source.parent, *roots]:
        candidate = (root / relative).resolve()
        if candidate.is_file():
            return candidate
    return None


def package_data_the_packaging_names() -> list[str]:
    """Every package-data entry pyproject.toml names."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        packaging = tomllib.load(handle)
    declared = packaging.get("tool", {}).get("setuptools", {}).get("package-data", {})
    return [entry for entries in declared.values() for entry in entries]


def configuration_problems(groups) -> tuple[list[str], list[str], list[str]]:
    """How the build files disagree with the tree.

    The sources include their neighbours by bare file name, so the groups are
    include roots; a group that exists and is not a root is a group whose files
    cannot see each other, which is exactly the mistake a move makes.  Package data
    is held to the same standard from the other side: naming a file that is not
    there ships a broken record of what the package contains.
    """
    expected = {"src"} | {"src/%s" % group for group in groups if group != "."}
    declared = set(include_roots_the_build_declares())
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)

    absent = sorted(
        name
        for name in package_data_the_packaging_names()
        if name not in GENERATED_PACKAGE_DATA and not (PACKAGE_ROOT / name).exists()
    )
    return missing, extra, absent


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the binding's source layout.")
    parser.add_argument("--enforce", action="store_true",
                        help="exit non-zero when the tree is out of place")
    parser.add_argument("--list", action="store_true", dest="list_files",
                        help="print the files of each group")
    arguments = parser.parse_args()

    paths = source_files()
    owners: dict[str, list[Path]] = {}
    for path in paths:
        owners.setdefault(path.name, []).append(path)

    groups: dict[str, list[Path]] = {}
    for path in paths:
        groups.setdefault(group_of(path), []).append(path)

    if arguments.list_files:
        for group in sorted(groups, key=lambda name: (LAYERS.get(name, 99), name)):
            entries = groups[group]
            print(f"{group} ({len(entries)} files)")
            for entry in entries:
                print(f"  {entry.relative_to(SOURCE_ROOT)}")
        print(f"{len(paths)} files in {len(groups)} groups")
        return 0

    unknown = sorted(group for group in groups if group not in LAYERS)
    duplicates = sorted(name for name, where in owners.items() if len(where) > 1)

    relative_parents: list[str] = []
    unresolved: list[str] = []
    upward: list[str] = []
    allowed_upward: list[str] = []
    edges = 0
    try:
        declared_roots = include_roots_the_build_declares()
    except (SyntaxError, ValueError) as error:
        print(f"cannot read Extension.include_dirs: {error}", file=sys.stderr)
        return 1
    include_roots = [SOURCE_ROOT / Path(root).relative_to('src')
                     if Path(root).parts and Path(root).parts[0] == 'src' else PROJECT_ROOT / root
                     for root in declared_roots]
    known_paths = {path.resolve(): path for path in paths}

    for path in paths:
        group = group_of(path)
        layer = LAYERS.get(group)
        for number, included in includes_of(path):
            resolved = resolve_include(path, included, include_roots)
            destination = known_paths.get(resolved)
            name = Path(included.replace('\\', '/')).name
            if destination is None:
                unresolved.append(f"{path.relative_to(SOURCE_ROOT)}:{number}  \"{included}\"")
                continue
            edges += 1
            if name == "pyv8.h" and path.relative_to(SOURCE_ROOT).as_posix() != "pyv8.cpp":
                upward.append(f"{path.relative_to(SOURCE_ROOT)}:{number}  module composition header is private")
            if included.replace("\\", "/").startswith("../"):
                relative_parents.append(f"{path.relative_to(SOURCE_ROOT)}:{number}  \"{included}\"")
            if layer is None:
                continue
            target = LAYERS.get(group_of(destination))
            if target is None or target <= layer:
                continue
            entry = (
                f"{path.relative_to(SOURCE_ROOT)}:{number}  \"{included}\""
                f"  (layer {layer} -> {target})"
            )
            source_key = path.relative_to(SOURCE_ROOT).as_posix()
            target_key = destination.relative_to(SOURCE_ROOT).as_posix()
            if target_key in COMPOSITION_EDGES.get(source_key, ()):
                allowed_upward.append(entry)
            else:
                upward.append(entry)

    print(f"layout check: {len(paths)} files in {len(groups)} groups, {edges} local include edges")
    roots_missing, roots_extra, data_absent = configuration_problems(groups)
    for title, entries in (
        ("directories the layout does not name", unknown),
        ("header names that are not unique", duplicates),
        ("includes that reach up to a layer above the file", upward),
        ("includes that reach up, allowed by a composition point", allowed_upward),
        ("includes that reach up with ../", relative_parents),
        ("quoted includes that match no file in the tree", unresolved),
        ("groups setup.py does not offer as include roots", roots_missing),
        ("include roots setup.py offers that no group needs", roots_extra),
        ("package data pyproject.toml names that the tree does not have", data_absent),
    ):
        print(f"  {title}: {len(entries)}")
        for entry in entries:
            print(f"    {entry}")

    violations = (
        len(unknown) + len(duplicates) + len(upward) + len(relative_parents) + len(unresolved)
        + len(roots_missing) + len(roots_extra) + len(data_absent)
    )
    if violations == 0:
        print("layout ok")
        return 0
    if arguments.enforce:
        print(
            f"layout is out of place in {violations} place(s); see docs/pyv8/source-layout.md",
            file=sys.stderr,
        )
        return 1
    print("reporting only; pass --enforce to fail on these")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
