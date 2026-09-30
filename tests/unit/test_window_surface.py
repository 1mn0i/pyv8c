"""The window surface narrows the baseline to what the reference browser installs."""
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


def test_a_surface_narrows_window_members_and_leaves_other_interfaces_whole(baseline):
    surface = WindowSurface(chrome="test", own_names=frozenset({"onclick"}))
    table = InterfaceTable(baseline, surface=surface)

    assert {member.name for _location, member in table.own_members("Window")} == {"onclick"}
    assert table.member("Window", "sharedStorage", "attribute") is None
    assert len(table.own_members("Document")) == len(
        InterfaceTable(baseline).own_members("Document")
    )


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

    names = {
        member.name
        for _location, member in InterfaceTable(baseline, surface=surface).own_members("Window")
    }
    assert not names & {"sharedStorage", "printing", "ontouchstart", "testOriginTrialGlobalAttribute"}
    assert names <= surface.own_names


def test_the_surface_is_a_section_of_the_profile_the_environment_reads():
    """One collection run states both, so the file and the reader cannot disagree."""
    from pyv8c.foundation.environment import PROFILE

    assert PROFILE_PATH == PROFILE
    payload = json.loads(PROFILE.read_text(encoding="utf-8"))
    surface = load_window_surface()

    assert surface is not None
    assert surface.chrome == payload["source"]["browser"]
    assert surface.own_names == frozenset(payload["window_surface"]["own_names"])


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
