"""The system font index: one table answers existence, names and measurement."""

import os

import pytest

from pyv8c.foundation import system_fonts


def _records():
    records = system_fonts.records()
    if not records:
        pytest.skip("this machine has no fonts to index")
    return records


def test_a_face_appears_once_under_its_postscript_name():
    records = _records()
    names = [system_fonts.normalize(record.postscript_name) for record in records]
    assert len(names) == len(set(names))


def test_a_lookup_folds_case_and_runs_of_space():
    records = _records()
    record = next((item for item in records if item.full_name), None)
    if record is None:
        pytest.skip("no face here names itself in full")
    assert system_fonts.lookup_in(records, record.full_name.upper()) is record
    assert system_fonts.lookup_in(records, f"  {record.full_name}  ") is record
    assert system_fonts.lookup_in(records, "") is None
    assert system_fonts.lookup_in(records, "Definitely Not A Font 123") is None


def test_a_postscript_name_is_tried_before_a_full_name():
    """One face can hold another's PostScript name; Chromium tries PostScript first."""
    table = (
        system_fonts.FontRecord("Shared", "Full Name", "Family", "Regular", "a.ttf", 0),
        system_fonts.FontRecord("Other", "Shared", "Family", "Regular", "b.ttf", 0),
    )
    found = system_fonts.lookup_in(table, "Shared")
    assert found is not None and found.postscript_name == "Shared"


def test_a_collection_face_is_a_face_of_its_own():
    records = _records()
    collected = [item for item in records if item.index]
    if not collected:
        pytest.skip("this machine has no font collection")
    for record in collected:
        assert record.path.lower().endswith((".ttc", ".otc"))
        assert record.index > 0
    assert system_fonts.lookup_in(records, collected[0].postscript_name) is collected[0]


def test_a_file_name_resolves_to_the_file_this_machine_has():
    """A profile names the file, not where it is; the roots decide where that is here."""
    records = _records()
    record = next((item for item in records if item.path), None)
    if record is None:
        pytest.skip("this machine has no fonts to index")
    assert system_fonts.file_for(record) == record.path
    travelled = system_fonts.FontRecord(record.postscript_name, record.full_name, record.family,
                                        record.style, os.path.basename(record.path), record.index)
    assert system_fonts.file_for(travelled) == record.path


def test_a_face_whose_file_is_absent_has_no_file():
    """Names are the profile's to answer; only measurement needs a file, and None says so."""
    assert system_fonts.file_for(system_fonts.FontRecord("NoSuch-Regular", "No Such", "No Such",
                                                         "Regular", "", 0)) is None
    assert system_fonts.file_for(system_fonts.FontRecord("NotHere-Regular", "Not Here", "Not Here",
                                                         "Regular", "nothere-9001.ttf", 0)) is None


def test_a_face_whose_file_moved_is_found_by_name():
    """A file that was renamed is the same face, which the PostScript name still knows."""
    named = [item for item in _records() if item.postscript_name]
    if not named:
        pytest.skip("this machine has no named face")
    record = named[0]
    moved = system_fonts.FontRecord(record.postscript_name, record.full_name, record.family,
                                    record.style, "renamed-file-9001.ttf", record.index)
    assert system_fonts.file_for(moved) == record.path


def test_a_family_lists_every_face_it_has():
    records = _records()
    family = records[0].family
    found = system_fonts.family_records(family)
    assert found and all(system_fonts.normalize(item.family) == system_fonts.normalize(family)
                         for item in found)
    assert system_fonts.match_family(family) is found[0]
    assert system_fonts.families() == tuple(sorted(set(system_fonts.families())))


def test_a_profile_replaces_this_machine():
    """A profile that carries a font set answers for the page instead of this machine."""
    from pyv8c.foundation.environment import BrowserEnvironment

    entry = {"postscript_name": "OnlyHere-Regular", "full_name": "Only Here",
             "family": "Only Here", "style": "Regular"}
    environment = BrowserEnvironment({"fonts.records": [entry]})
    assert len(environment.fonts.resolved()) == 1
    assert system_fonts.table_of(environment) is environment.fonts.records
    assert system_fonts.lookup_in(system_fonts.table_of(environment), "only here") is not None
    assert len(system_fonts.table_of(None)) == len(system_fonts.records())
