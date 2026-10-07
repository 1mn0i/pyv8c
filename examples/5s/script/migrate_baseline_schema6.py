"""Rewrite the shipped baselines into schema 6, where ``array`` is a word instead of a boolean.

    python312 script/migrate_baseline_schema6.py --check   # report, change nothing
    python312 script/migrate_baseline_schema6.py           # rewrite the snapshots in place

Why a migration at all: schema 6 states what an interface borrows from ``Array.prototype`` as
the word ``IdlSpec.array`` takes (``"iterator"`` / ``"iterable"``, key absent when it borrows
nothing), and a real regeneration needs a Chromium source tree this machine does not have
(``CHROMIUM_ROOT``).  So the three shipped snapshots are rewritten from what they already carry:

* the four named iterator methods declared as **operations** -- a Maplike/Setlike surface -- make
  an interface ``"iterable"``;
* schema 5's boolean, an installed intrinsic, makes it ``"iterator"``;
* and the package snapshot, which describes the very release the measured profile was taken from,
  takes its words from that two-sided reading where it exists.  That is what fills in the twelve
  interfaces whose IDL says ``iterable<>``/``maplike<>`` but whose generated tables never list the
  four (``NodeList``, ``DOMTokenList``, ``StylePropertyMap``, ...), and it is also what drops the
  three the tables over-claimed (``FileSystemDirectoryHandle`` answers ``@@asyncIterator`` alone).
  The next regeneration with ``--idl-root`` computes all of it from the IDL and needs none of this.

The package snapshot's digest changes with its bytes, so the profile's ``baseline_digest`` is
rewritten too: without that, ``_measured_against`` would quietly stop applying every gap.

It lives beside the case, not in ``pyv8c/tools``, because the package snapshot's words come from
the two-sided reading this directory keeps (``surface-surface_wide-chrome.json``): the generated
tables cannot state what the IDL says (``NodeList`` answers the four without listing them), so the
release being migrated is credited with what it was measured to publish.  The next real
regeneration with ``--idl-root`` computes that from the IDL and this file is then dead weight.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PYV8C = HERE.parents[2]
sys.path.insert(0, str(PYV8C))

from pyv8c.engine.baseline import BASELINE_SCHEMA, BaselineRepository, write_baseline  # noqa: E402

FOUR = ("entries", "keys", "values", "forEach")
PACKAGE_ZIP = PYV8C / "pyv8c" / "baselines" / "interface-baseline.zip"
SIDECAR_JSON = PYV8C / "pyv8c" / "baselines" / "interface-baseline.json"
BINARY_CACHE = PYV8C / "pyv8c" / "baselines" / "interface-baseline.pyv8ccache"
PROFILE = PYV8C / "pyv8c" / "data" / "chrome-environment.json"
VERSION_ZIPS = (PYV8C / "baselines" / "145.0.7632.162.zip",
                PYV8C / "baselines" / "154.0.8030.0.zip")
MEASURED = HERE.parent / "surface-surface_wide-chrome.json"


def payload_bytes(document):
    """The bytes ``write_baseline`` writes, so the digest can be computed from the same ones."""
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def read_snapshot(path):
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".json"))
        return json.loads(archive.read(member).decode("utf-8"))


def measured_shapes(path):
    """``{interface: word or None}`` from a two-sided reading of a real Chrome."""
    if not path.is_file():
        return {}
    found = {}
    for holder, members in json.loads(path.read_text(encoding="utf-8")).items():
        if not holder.endswith(".prototype"):
            continue
        names = {name for name in members if not name.startswith("__")}
        if all(name in names for name in FOUR):
            found[holder[: -len(".prototype")]] = "iterable"
        elif "@@iterator" in names:
            found[holder[: -len(".prototype")]] = "iterator"
        else:
            found[holder[: -len(".prototype")]] = None
    return found


def shape_from_tables(entry):
    """The word the snapshot's own facts justify, without any measurement."""
    operations = {member.get("name")
                  for member in ((entry.get("members") or {}).get("prototype") or [])
                  if member.get("kind") == "operation"}
    if set(FOUR) <= operations:
        return "iterable"
    return "iterator" if (entry.get("template") or {}).get("array") else None


def migrate(document, measured, *, trust_measured):
    """Rewrite one snapshot's ``template.array`` in place; return how many of each word."""
    document["_webidl"]["schema"] = BASELINE_SCHEMA
    counts = {"iterable": 0, "iterator": 0, "none": 0}
    for name, entry in document.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        word = (measured.get(name, shape_from_tables(entry)) if trust_measured
                else shape_from_tables(entry))
        template = entry.setdefault("template", {})
        template.pop("array", None)
        if word:
            template["array"] = word
        counts[word or "none"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only, change nothing")
    arguments = parser.parse_args(argv)
    measured = measured_shapes(MEASURED)
    if not measured:
        print(f"! no measured reading at {MEASURED}: the package snapshot would lose the twelve")

    targets = ((PACKAGE_ZIP, True), *((path, False) for path in VERSION_ZIPS))
    package_digest = None
    for path, trust in targets:
        if not path.is_file():
            print(f"  {path.name}: absent, skipped")
            continue
        document = read_snapshot(path)
        counts = migrate(document, measured, trust_measured=trust)
        payload = payload_bytes(document)
        digest = hashlib.sha256(payload).hexdigest()
        print(f"  {path.name}: schema -> {BASELINE_SCHEMA}, iterable {counts['iterable']},"
              f" iterator {counts['iterator']}, none {counts['none']}, sha256 {digest[:16]}")
        if trust:
            package_digest = digest
        if not arguments.check:
            write_baseline(path, document, compress=True)

    if arguments.check:
        print("  --check: nothing written")
        return 0

    for derived in (SIDECAR_JSON, BINARY_CACHE):
        if derived.is_file():
            derived.unlink()
            print(f"  removed derived {derived.name} (rebuilt from the zip on next load)")
    if package_digest is not None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        surface = profile["window_surface"]
        if surface.get("baseline_digest") != package_digest:
            surface["baseline_digest"] = package_digest
            PROFILE.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            print(f"  profile baseline_digest -> {package_digest[:16]}")

    snapshot = BaselineRepository.load()
    assert snapshot.metadata["schema"] == BASELINE_SCHEMA, snapshot.metadata["schema"]
    if package_digest is not None:
        assert snapshot.digest == package_digest, (snapshot.digest[:16], package_digest[:16])
    print(f"  reloaded: schema {snapshot.metadata['schema']}, {len(snapshot.interfaces)} interfaces,"
          f" digest {snapshot.digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
