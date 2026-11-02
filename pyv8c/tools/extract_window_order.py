"""Write the Window installation order one Chromium release gives its realm.

The interface baseline records which interfaces exist, not the order Blink
installs them in: its ``interfaces`` object is name-sorted, and it carries no
runtime-enabled or SecureContext metadata.  This tool reads the same baseline
plus the Chromium IDL sources and writes the order as a small document next to
the bundled environment profile, so a realm can reproduce it without a Chromium
checkout.

Template membership is derived from IDL. Conditional constructor ordering and
its split into phases require a separately recorded same-version reference:

* an interface that is ``[SecureContext]`` or ``[RuntimeEnabled]`` is installed
  by Blink's conditional pass, after the Window members;
* every other interface is installed by the template pass, before them, in
  descending name order;
* the Window members are grouped by the baseline's ``kind`` and
  ``install_phase``: ordinary attributes, later-template attributes, operations,
  then the context-dependent ``object`` members.

The three legacy factory functions and the six ``[LegacyWindowAlias]`` names are
the one table this tool states itself -- they are not members of an interface
the baseline carries, and their order is part of the observable surface.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..engine.idl import Idl
from .extract_idl import DEFAULT_CHROMIUM_ROOT, read_chromium_version

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "pyv8c" / "data" / "window-order.json"
DEFAULT_REFERENCE = PROJECT_ROOT / "pyv8c" / "data" / "window-order-conditional-reference.json"

#: The implementation version of the document this tool writes.
ORDER_VERSION = 1

#: The names Blink installs before the interface constructors, in its order.
LEGACY_FACTORIES = (
    "Option", "Image", "Audio",
    "webkitURL", "webkitRTCPeerConnection", "webkitMediaStream",
    "WebKitMutationObserver", "WebKitCSSMatrix", "FeaturePolicy",
)

#: ``interface`` and ``namespace`` declarations, partial or not, with their attributes.
#: Attributes are read only up to the first brace, so a member's own extended
#: attributes are never mistaken for the interface's.
_DECLARATION = re.compile(
    r"(?:(?P<attrs>\[[^\]{}]*\])\s*)?(?P<partial>\bpartial\s+)?"
    r"(?:callback\s+)?(?:interface(?:\s+mixin)?|namespace)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z_][A-Za-z0-9_]*)?\s*\{")


def declarations(idl_root: Path) -> dict:
    """``name -> interface attributes`` for every non-partial declaration under *root*.

    A partial declaration may carry a condition, but Blink's exposure decision is
    made from the interface itself; merging a partial in would mark ``Document``
    conditional because one of its supplements is.
    """
    found: dict[str, str] = {}
    for path in sorted(idl_root.rglob("*.idl")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _DECLARATION.finditer(text):
            if match.group("partial"):
                continue
            found.setdefault(match.group("name"), match.group("attrs") or "")
    return found


def conditional(name: str, attrs: dict) -> bool:
    """Whether Blink installs *name* in its conditional pass.

    A name the IDL sources do not declare is a namespace the baseline carries
    without a wrapper declaration (``CSS``, the ``GPU*`` namespaces); those are
    conditional too.  Every ``[RuntimeEnabled]`` interface is conditional: a
    stable feature still installs behind the template pass, which is why Chrome
    154 leaves ``WebSocketStream`` and its siblings after ``window``.
    """
    declared = attrs.get(name)
    if declared is None:
        return True
    if "SecureContext" in declared:
        return True
    if re.search(r"RuntimeEnabled\s*=", declared):
        return True
    # ``Exposed(Window FeatureName)`` is a conditional exposure too.
    for group in re.findall(r"Exposed\s*(?:=\s*)?\(([^)]*)\)", declared):
        for entry in group.split(","):
            words = entry.split()
            if len(words) > 1 and words[0] == "Window":
                return True
    return False


#: Window operations Blink installs after the conditional constructors.  The
#: baseline states them as object-phase operations, but the reference release
#: puts their install call after ``InstallExposedConstructs``, so they close the
#: list.
TAIL_MEMBERS = ("requestResize", "webkitRequestFileSystem", "webkitResolveLocalFileSystemURL")


def member_groups(idl) -> dict:
    """The Window member groups, as the baseline's own fields and Blink state them."""
    members = idl.interface("Window").own_members("instance")
    early, late_template, operations, late_attributes, late_operations, tail = [], [], [], [], [], []
    for member in members:
        if member.name in TAIL_MEMBERS:
            tail.append(member.name)
        elif member.install_phase == "object":
            (late_attributes if member.kind == "attribute" else late_operations).append(member.name)
        elif member.kind == "operation":
            operations.append(member.name)
        elif member.install_phase == "template_late":
            late_template.append(member.name)
        else:
            early.append(member.name)
    return {
        "early": early,
        "operations": operations,
        "late_attributes": late_attributes,
        "late_operations": late_operations,
        "late_template": late_template,
        "tail": tail,
    }


def build(idl, attrs: dict, chromium_version: str, reference: dict) -> dict:
    """The order document for one decoded baseline and its IDL declarations."""
    names = tuple(idl.exposed("Window"))
    conditional_names = {name for name in names if conditional(name, attrs)}
    before = sorted((name for name in names if name not in conditional_names), reverse=True)
    if reference.get("chromium_version") != chromium_version:
        raise ValueError("the conditional-order reference must match the Chromium release")
    after = reference.get("constructors_after")
    after_late = reference.get("constructors_after_late")
    if not isinstance(after, list) or not isinstance(after_late, list):
        raise ValueError("the conditional-order reference must contain both constructor phases")
    recorded = after + after_late
    if any(not isinstance(name, str) for name in recorded) or len(set(recorded)) != len(recorded):
        raise ValueError("the conditional-order reference contains invalid or duplicate names")
    if set(recorded) != conditional_names:
        raise ValueError(f"conditional-order reference differs from IDL: missing={sorted(conditional_names - set(recorded))!r}, extra={sorted(set(recorded) - conditional_names)!r}")
    groups = member_groups(idl)
    total = sum(len(group) for group in groups.values())
    if total != len(idl.interface("Window").own_members("instance")):
        raise ValueError("a Window member belongs to no group")
    return {
        "version": ORDER_VERSION,
        "chromium_version": chromium_version,
        "legacy_factories": list(LEGACY_FACTORIES),
        "constructors_before": before,
        "constructors_after": after,
        "constructors_after_late": after_late,
        "members": groups,
        "note": "Conditional constructor phases follow the same-release recorded reference.",
    }


def main():
    parser = argparse.ArgumentParser(description="Write the Window installation order for one Chromium release.")
    parser.add_argument("output", type=Path, nargs="?", default=DEFAULT_OUTPUT,
                        help="output path (default: %(default)s)")
    parser.add_argument("--idl", type=Path, default=PROJECT_ROOT / "pyv8c" / "idl" / "idl.zip",
                        help="IDL baseline to read (default: %(default)s)")
    parser.add_argument("--chromium-root", type=Path, default=DEFAULT_CHROMIUM_ROOT,
                        help="Chromium checkout root holding src/ (default: %(default)s)")
    parser.add_argument("--chromium-version", default=None,
                        help="release the IDL sources came from (default: read chrome/VERSION)")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                        help="same-release conditional constructor ordering reference")
    args = parser.parse_args()

    idl = Idl.load(args.idl)
    version = args.chromium_version or read_chromium_version(args.chromium_root)
    if idl.metadata.get("chromium_version") != version:
        raise SystemExit(
            f"the baseline states Chromium {idl.metadata.get('chromium_version')!r},"
            f" the IDL sources are {version!r}; regenerate the baseline first")
    idl_root = args.chromium_root / "src" / "third_party" / "blink" / "renderer"
    if not idl_root.is_dir():
        raise SystemExit(f"the Chromium IDL sources were not found under {idl_root}")
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    document = build(idl, declarations(idl_root), version, reference)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(document['constructors_before'])} before,"
          f" {len(document['constructors_after'])} after,"
          f" {sum(len(group) for group in document['members'].values())} Window members")


if __name__ == "__main__":
    main()
