"""The order Blink gives a Window realm's own properties.

The order is data, not a rule: ``idl.json`` carries no runtime-enabled or
SecureContext metadata, so which interfaces install before the Window members,
and which install after them, is recorded once per Chromium release in
``data/window-order.json``.  :func:`load` answers ``None`` when the file is
missing; invalid or unreadable data is reported to the baseline mismatch policy.

Nothing here imports the native binding: the offline extractor writes the file,
and a realm reads it as part of its plan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORDER_PATH = PACKAGE_ROOT / "data" / "window-order.json"

#: The document this build understands; another is reported by the reader.
ORDER_VERSION = 1

#: The member groups and the order Blink installs them in.  ``early`` holds the
#: ordinary attributes, ``operations`` the methods, ``late_attributes`` and
#: ``late_operations`` the context-dependent phases, ``late_template`` the members
#: whose IDL marks a later template pass, and ``tail`` the conditional operations
#: Blink installs last.  Chrome 154 splits the conditional phase as
#: attributes -> constructors -> operations -> template-late -> tail.
GROUPS = ("early", "operations", "late_attributes", "late_operations",
          "late_template", "tail")


@dataclass(frozen=True)
class WindowOrder:
    """One release's Window installation order, as the document states it."""

    chromium_version: str
    legacy_factories: tuple[str, ...]
    #: The interfaces installed before the Window members, in descending name order.
    constructors_before: tuple[str, ...]
    #: The conditional interfaces installed after them, in the reference browser's order.
    constructors_after: tuple[str, ...]
    #: ``group -> member names`` for the Window member groups.
    members: dict[str, tuple[str, ...]]
    #: The conditional interfaces Blink resumes after the conditional operations.
    constructors_after_late: tuple[str, ...] = ()

    def group(self, name: str) -> tuple[str, ...]:
        """The members of one group, empty when the document does not state it."""
        return self.members.get(name, ())

    @property
    def preinstall(self) -> tuple[str, ...]:
        """The names installed before the Window members: factories, then interfaces."""
        return self.legacy_factories + self.constructors_before

    @property
    def postinstall(self) -> tuple[str, ...]:
        """The names installed after every Window group."""
        return self.constructors_after


def load(path=None) -> WindowOrder | None:
    """Read the order document at *path*, or ``None`` when it is absent.

    A missing file is not an error: a Browser built against a document that was
    generated without the order data keeps the order the document implies.
    """
    target = Path(path) if path is not None else DEFAULT_ORDER_PATH
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
        return decode(document)
    except FileNotFoundError:
        return None


def decode(document) -> WindowOrder:
    """The order a decoded document states; a malformed one is refused by name."""
    if not isinstance(document, dict):
        raise ValueError("the Window order document must be an object")
    version = document.get("version")
    if version != ORDER_VERSION:
        raise ValueError(f"unsupported Window order version {version!r}; expected {ORDER_VERSION}"
                         " -- regenerate it with tools/extract_window_order.py")
    version_name = document.get("chromium_version")
    if not isinstance(version_name, str) or not version_name:
        raise ValueError("the Window order document must name the Chromium release it came from")

    def names(key, *, ordered=None):
        value = document.get(key)
        if not isinstance(value, list) or any(not isinstance(name, str) or not name for name in value):
            raise ValueError(f"the Window order document must state {key!r} as a list of names")
        if len(set(value)) != len(value):
            raise ValueError(f"the Window order document repeats a name in {key!r}")
        if ordered is not None and tuple(value) != tuple(sorted(value, reverse=ordered)):
            raise ValueError(f"the Window order document states {key!r} out of order")
        return tuple(value)

    groups = document.get("members")
    if not isinstance(groups, dict):
        raise ValueError("the Window order document must state 'members' as an object")
    members = {}
    for group in GROUPS:
        members[group] = names_in(groups, group)
    result = WindowOrder(
        chromium_version=version_name,
        legacy_factories=names("legacy_factories"),
        constructors_before=names("constructors_before", ordered=True),
        #: The conditional block follows the reference browser's installation
        #: order, which is not the name order: the measured 154 list puts
        #: ``AnimationTrigger`` after ``AudioWorklet``, for one.
        constructors_after=names("constructors_after"),
        members=members,
        constructors_after_late=(names("constructors_after_late")
                                 if "constructors_after_late" in document else ()),
    )
    sections = (result.legacy_factories, result.constructors_before,
                result.constructors_after, result.constructors_after_late,
                *result.members.values())
    seen = set()
    for section in sections:
        duplicates = seen.intersection(section)
        if duplicates:
            raise ValueError(f"the Window order document repeats names across groups: {sorted(duplicates)!r}")
        seen.update(section)
    return result


def names_in(document, key) -> tuple[str, ...]:
    """A member group, validated the same way as the top-level lists."""
    value = document.get(key)
    if not isinstance(value, list) or any(not isinstance(name, str) or not name for name in value):
        raise ValueError(f"the Window order document must state members.{key} as a list of names")
    if len(set(value)) != len(value):
        raise ValueError(f"the Window order document repeats a name in members.{key}")
    return tuple(value)
