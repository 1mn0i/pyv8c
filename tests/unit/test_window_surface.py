"""The measured surface narrows the baseline to what the reference browser installs."""
import json

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.baseline import (  # noqa: E402
    PROFILE_PATH,
    BaselineRepository,
    BaselineSnapshot,
    InterfaceTable,
    WindowSurface,
    load_window_surface,
)


@pytest.fixture(scope="module")
def baseline():
    return BaselineRepository.load()


def test_a_window_reading_narrows_window_members(baseline):
    surface = WindowSurface(chrome="test", own_names=frozenset({"onclick"}))
    table = InterfaceTable(baseline, surface=surface)

    assert {member.name for _location, member in table.own_members("Window")} == {"onclick"}
    assert table.member("Window", "sharedStorage", "attribute") is None


def test_a_measured_word_overrides_the_baselines(baseline):
    """The baseline carries every platform state; the profile says what this release installs.

    ``NodeList`` borrows the iterator with the four named methods and ``HTMLCollection`` the
    iterator alone, both measured against Chrome 154 on 2026-10-07.  A release that published no
    iterator for one of them records nothing, which is how the word in force becomes *narrower*
    than the baseline's rather than wider.
    """
    assert InterfaceTable(baseline).interface("NodeList").array == "iterable"
    assert InterfaceTable(baseline).interface("HTMLCollection").array == "iterator"

    surface = WindowSurface(chrome="test", own_names=frozenset(),
                            array_words={"NodeList": "iterator", "Unknown": "iterable"})
    table = InterfaceTable(baseline, surface=surface)

    assert table.interface("NodeList").array == "iterator"
    # An interface the profile says nothing about keeps the baseline's word…
    assert table.interface("HTMLCollection").array == "iterator"
    # …and a name the baseline never declared has no interface to give a word to.
    assert "Unknown" not in table.names()


def test_a_profile_of_another_release_does_not_apply_its_words(baseline):
    """A word describes one release, so a digest that does not match says nothing."""
    surface = WindowSurface(chrome="test", own_names=frozenset(),
                            array_words={"NodeList": "iterator"},
                            baseline_digest="another-release")
    table = InterfaceTable(baseline, surface=surface)

    assert table.interface("NodeList").array == "iterable"


def test_a_surface_without_prototype_readings_leaves_other_interfaces_whole(baseline):
    """A profile written before the prototype section existed must not lose any member."""
    surface = WindowSurface(chrome="test", own_names=frozenset({"onclick"}))
    table = InterfaceTable(baseline, surface=surface)

    assert len(table.own_members("Document")) == len(
        InterfaceTable(baseline).own_members("Document")
    )


def test_a_prototype_gap_narrows_only_that_location(baseline):
    """A gap names members the browser leaves out; other locations are not prototypes."""
    raw = InterfaceTable(baseline).own_members("Document")
    prototype = {member.name for location, member in raw if location == "prototype"}
    other = {(location, member.name) for location, member in raw if location != "prototype"}
    assert prototype and other, "this test needs an interface with both kinds of member"
    omitted = sorted(prototype)[0]

    surface = WindowSurface(chrome="test", own_names=frozenset({"onclick"}),
                            prototype_gaps={"Document": frozenset({omitted})})
    table = InterfaceTable(baseline, surface=surface)

    kept = {(location, member.name) for location, member in table.own_members("Document")}
    assert ("prototype", omitted) not in kept
    assert kept == other | {("prototype", name) for name in prototype - {omitted}}


def test_an_interface_the_browser_was_not_asked_about_keeps_every_location(baseline):
    surface = WindowSurface(chrome="test", own_names=frozenset({"onclick"}),
                            prototype_gaps={"Document": frozenset({"querySelector"})})

    assert len(InterfaceTable(baseline, surface=surface).own_members("Node")) == len(
        InterfaceTable(baseline).own_members("Node")
    )


def test_a_gap_only_applies_to_the_baseline_it_was_measured_against(baseline):
    """The names mean nothing against another release, so they are dropped, not applied."""
    elsewhere = WindowSurface(chrome="test", own_names=frozenset({"onclick"}),
                              prototype_gaps={"Document": frozenset({"querySelector"})},
                              baseline_digest="measured-against-another-release")
    measured = WindowSurface(chrome="test", own_names=frozenset({"onclick"}),
                             prototype_gaps={"Document": frozenset({"querySelector"})},
                             baseline_digest=baseline.digest)

    ignored = InterfaceTable(baseline, surface=elsewhere).own_members("Document")
    applied = InterfaceTable(baseline, surface=measured).own_members("Document")

    assert "querySelector" in {member.name for _location, member in ignored}
    assert "querySelector" not in {member.name for _location, member in applied}


def test_a_gap_stops_the_descendant_search():
    """A name the browser leaves out is not recovered from a subclass."""
    members = {"prototype": [{"name": "omitted", "kind": "attribute"}]}
    baseline = BaselineSnapshot(path="test", digest="", metadata={}, interfaces={
        "Base": {"parent": None, "members": {"prototype": []}, "constants": []},
        "Child": {"parent": "Base", "members": members, "constants": []},
    })
    surface = WindowSurface("test", frozenset({"Base"}), prototype_gaps={"Base": frozenset({"omitted"})})
    table = InterfaceTable(baseline, surface=surface)

    assert table.member("Child", "omitted", "attribute") is not None
    assert table.member("Base", "omitted", "attribute") is None


def test_a_member_the_browser_leaves_out_is_still_declared(baseline):
    """Implementing it describes a difference between browsers, not a baseline one."""
    surface = load_window_surface()
    assert surface is not None
    if not surface.prototype_gaps:
        pytest.skip("the shipped profile carries no prototype gaps yet")
    table = InterfaceTable(baseline, surface=surface)

    assert table.member("Element", "ariaOwnsElements", "attribute") is None
    assert table.declares("Element", "ariaOwnsElements", "attribute")
    assert not table.declares("Element", "noSuchMember", "attribute")


def test_without_a_surface_every_baseline_member_stays(baseline):
    assert InterfaceTable(baseline).member("Window", "sharedStorage", "attribute") is not None


def test_a_surface_narrows_which_interfaces_a_window_installs(baseline):
    surface = WindowSurface(chrome="test", own_names=frozenset({"EventTarget", "Node", "Event"}))
    table = InterfaceTable(baseline, surface=surface)

    assert set(table.exposed("Window")) == {"EventTarget", "Node", "Event"}
    assert len(table.exposed("Worker")) == len(InterfaceTable(baseline).exposed("Worker"))


def test_window_aliases_need_the_baseline_field_and_the_reference_alike():
    baseline = BaselineSnapshot(path="test", digest="", metadata={}, interfaces={
        "URL": {"parent": None, "tag": "URL", "exposed_in": ["Window"], "aliases": ["webkitURL"],
                "constructor": {"exposed": True}, "template": {}, "members": {}, "constants": []},
    })
    both = InterfaceTable(baseline, surface=WindowSurface("test", frozenset({"URL", "webkitURL"})))
    only_target = InterfaceTable(baseline, surface=WindowSurface("test", frozenset({"URL"})))

    assert both.window_aliases() == {"webkitURL": "URL"}
    assert only_target.window_aliases() == {}


def test_the_committed_surface_drops_the_members_no_browser_installs(baseline):
    surface = load_window_surface()
    assert surface is not None and surface.chrome

    table = InterfaceTable(baseline, surface=surface)
    names = {member.name for _location, member in table.own_members("Window")}
    assert not names & {"sharedStorage", "printing", "ontouchstart", "testOriginTrialGlobalAttribute"}
    assert names <= surface.own_names

    # The same reading covers the prototypes, when the profile carries the gaps.
    if not surface.prototype_gaps:
        pytest.skip("the shipped profile carries no prototype gaps yet")
    element = {member.name for _location, member in table.own_members("Element")}
    assert not element & {"computedRole", "headingReset", "containerTiming", "afterHTML"}
    assert {"id", "slot", "part", "attributes"} <= element

    # Nothing the browser was measured to leave out is installed from this reading.
    for interface in ("Element", "Document", "HTMLElement", "HTMLIFrameElement"):
        omitted = surface.prototype_gaps.get(interface, frozenset())
        installed = {member.name for location, member in table.own_members(interface)
                     if location == "prototype"}
        assert not installed & omitted


def test_the_surface_is_a_section_of_the_profile_the_environment_reads():
    """One collection run states both, so the file and the reader cannot disagree."""
    from pyv8c.foundation.environment import PROFILE

    assert PROFILE_PATH == PROFILE
    payload = json.loads(PROFILE.read_text(encoding="utf-8"))
    surface = load_window_surface()

    assert surface is not None
    assert surface.chrome == payload["source"]["browser"]
    section = payload["window_surface"]
    assert surface.own_names == frozenset(section["own_names"])
    assert surface.prototype_gaps == {name: frozenset(names)
                                      for name, names in (section.get("prototype_gaps") or {}).items()}
    assert surface.interface_gaps == {name: frozenset(names)
                                      for name, names in (section.get("interface_gaps") or {}).items()}
    assert surface.array_words == {name: word
                                   for name, word in (section.get("array_words") or {}).items()
                                   if word in ("iterator", "iterable")}
    assert surface.baseline_digest == section.get("baseline_digest", "")


def test_a_profile_without_a_usable_surface_section_reads_none(tmp_path):
    names = ("absent", "empty", "wrongly shaped")
    for label in names:
        section = {"absent": None, "empty": {"own_names": []},
                   "wrongly shaped": {"own_names": "Object"}}[label]
        payload = {"schema": 1, "source": {"browser": "Chrome/1"}}
        if section is not None:
            payload["window_surface"] = section
        path = tmp_path / f"{label.replace(' ', '-')}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_window_surface(path) is None, label


def test_a_section_without_gaps_reads_as_no_measurements(tmp_path):
    """An older profile keeps every interface whole, which is the behavior it was read for."""
    payload = {"schema": 1, "source": {"browser": "Chrome/1"},
               "window_surface": {"keys": ["a"], "own_names": ["a"]}}
    path = tmp_path / "no-gaps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    surface = load_window_surface(path)

    assert surface is not None
    assert surface.prototype_gaps == {}
    assert surface.baseline_digest == ""


def test_an_interface_gap_narrows_the_interface_object_only(baseline):
    """``CSS`` carries ``animationWorklet`` on itself, not on a prototype, and Chrome leaves it
    out -- measured against Chrome 154 on 2026-10-07 with ``examples/5s/script/surface_diff.py``
    (which is how the two extra getters were found at all)."""
    raw = InterfaceTable(baseline).own_members("CSS")
    on_object = {member.name for location, member in raw if location == "interface"}
    on_prototype = {member.name for location, member in raw if location == "prototype"}
    assert "animationWorklet" in on_object, "this test needs the baseline as it is"
    assert on_object & {"supports", "escape"}, "and the members Chrome does install"

    surface = WindowSurface(chrome="test", own_names=frozenset({"CSS"}),
                            interface_gaps={"CSS": frozenset({"animationWorklet"})})
    table = InterfaceTable(baseline, surface=surface)

    kept = {member.name for _location, member in table.own_members("CSS")}
    assert "animationWorklet" not in kept
    assert {"supports", "escape"} <= kept
    # A prototype name is not evidence about the interface object, so it stays where it was.
    assert table.member("CSS", "animationWorklet", "operation") is None
    assert on_prototype == {member.name for location, member in table.own_members("CSS")
                            if location == "prototype"}


def test_a_wrongly_shaped_gap_entry_is_not_trusted(tmp_path):
    payload = {"schema": 1, "source": {"browser": "Chrome/1"},
               "window_surface": {"keys": ["a"], "own_names": ["a"],
                                  "prototype_gaps": {"Element": "slot", "Node": ["appendChild"]}}}
    path = tmp_path / "broken-gaps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    surface = load_window_surface(path)

    assert surface is not None
    assert surface.prototype_gaps.get("Element") is None
    assert surface.prototype_gaps["Node"] == frozenset({"appendChild"})
