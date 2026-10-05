"""The collector: what it refuses, and how it agrees with the field table."""
import json
import re

import pytest

from pyv8c.foundation.environment import COLLECTED_KEYS, BrowserEnvironment
from pyv8c.tools import collect_environment as collector

DOTTED_LITERAL = re.compile(r"'([a-z0-9_]+(?:\.[a-z0-9_]+)+)'")


def payload(**overrides):
    """A complete collected profile, as the browser would answer."""
    resolved = BrowserEnvironment().resolved()
    return {name: resolved[name] for name in COLLECTED_KEYS} | overrides


def test_the_script_asks_for_every_key_the_table_collects():
    asked = set(DOTTED_LITERAL.findall(collector.COLLECT_SCRIPT))
    dotted = {name for name in COLLECTED_KEYS if "." in name}
    assert asked == dotted
    for name in COLLECTED_KEYS:
        assert f"'{name}'" in collector.COLLECT_SCRIPT


def test_devtools_port_is_read_from_the_file_chrome_writes(tmp_path):
    (tmp_path / "DevToolsActivePort").write_text("49321\n/devtools/browser/abc\n", encoding="utf-8")
    assert collector.read_devtools_port(tmp_path, timeout=0.5) == (49321, "/devtools/browser/abc")

    with pytest.raises(collector.CollectionError, match="DevToolsActivePort"):
        collector.read_devtools_port(tmp_path / "elsewhere", timeout=0.05)


def test_the_browser_is_located_by_flag_then_environment_then_search(tmp_path, monkeypatch):
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    monkeypatch.setenv(collector.CHROME_ENV, str(browser))
    assert collector.find_chrome() == browser
    assert collector.find_chrome(str(browser)) == browser

    with pytest.raises(collector.CollectionError, match="does not exist"):
        collector.find_chrome(str(tmp_path / "missing"))

    monkeypatch.delenv(collector.CHROME_ENV)
    monkeypatch.setattr(collector, "CHROME_PATHS", (str(tmp_path / "absent"),))
    with pytest.raises(collector.CollectionError, match=collector.CHROME_ENV):
        collector.find_chrome()

    monkeypatch.setenv(collector.CHROME_ENV, str(tmp_path / "absent"))
    with pytest.raises(collector.CollectionError, match="does not exist"):
        collector.find_chrome()


def test_a_profile_has_to_be_complete_before_it_is_written():
    environment = collector.build_environment(payload(**{"platform": "Win32"}))
    assert environment.platform == "Win32"
    assert set(environment.to_dict()) == set(COLLECTED_KEYS)

    with pytest.raises(collector.CollectionError, match="missing \\['user_agent'\\]"):
        collector.build_environment({name: 1 for name in COLLECTED_KEYS if name != "user_agent"})
    with pytest.raises(collector.CollectionError, match="unknown \\['screen.nope'\\]"):
        collector.build_environment(payload(**{"screen.nope": 1}))
    with pytest.raises(TypeError, match="timezone"):
        collector.build_environment(payload(**{"timezone": None}))


def test_a_run_writes_the_environment_and_the_surface_together():
    """Both facts are read from one session, so they land in one document."""
    collected = payload()
    surface = collector.build_surface(
        {"keys": ["a"], "own": ["a", "b"],
         "prototypes": {"Node": ["appendChild", "cloneNode"], "Event": []}},
        {"Node": {"appendChild", "append", "cloneNode"}}, "digest-of-the-baseline")
    document = collector.build_profile(collected, surface, source={"browser": "Chrome/154.0.8037.95"})

    assert document["window_surface"] == {"keys": ["a"], "own_names": ["a", "b"],
                                          "prototype_gaps": {"Node": ["append"]},
                                          "baseline_digest": "digest-of-the-baseline"}
    assert document["source"]["browser"] == "Chrome/154.0.8037.95"
    assert set(document) == {"schema", "source", "window_surface", *COLLECTED_KEYS}
    # The section is not an environment key: the profile still loads as one.
    assert BrowserEnvironment.from_dict(document).to_dict() == collected


def test_a_surface_without_gap_readings_writes_empty_sections():
    """A browser that answered nothing about its prototypes narrows no interface."""
    surface = collector.build_surface({"keys": ["a"], "own": ["a"]}, {"Node": {"appendChild"}})

    assert surface["prototype_gaps"] == {}
    assert surface["baseline_digest"] == ""


def test_only_a_declared_name_can_be_a_gap():
    """What the baseline does not declare is not a difference, so it is not recorded."""
    surface = collector.build_surface({"keys": [], "own": [], "prototypes": {"Node": [],
                                                                            "Unknown": ["x"]}},
                                      {"Node": {"appendChild"}})

    assert surface["prototype_gaps"] == {"Node": ["appendChild"]}


def test_a_non_finite_number_is_written_as_its_name():
    """A bare ``Infinity`` is not JSON, so the profile spells it out."""
    collected = payload(**{"battery.discharging_time": float("inf")})
    document = collector.build_profile(collected, collector.build_surface({"keys": [], "own": []}),
                                       source={"browser": "Chrome/1"})

    assert document["battery.discharging_time"] == "Infinity"
    assert json.dumps(document, allow_nan=False)


def test_the_surface_script_asks_for_what_the_section_carries():
    assert "Object.keys(window)" in collector.SURFACE_SCRIPT
    assert "Object.getOwnPropertyNames(window)" in collector.SURFACE_SCRIPT
    assert "getOwnPropertyNames(prototype)" in collector.SURFACE_SCRIPT
    assert "prototypes" in collector.SURFACE_SCRIPT


def test_the_default_output_is_the_profile_the_package_ships():
    assert collector.DEFAULT_OUTPUT.name == "chrome-environment.json"
    assert collector.DEFAULT_OUTPUT.parent.name == "data"
    assert collector.DEFAULT_OUTPUT.exists()


def test_a_failed_collection_exits_nonzero_with_the_reason(monkeypatch, capsys):
    def refuse(explicit=None):
        raise collector.CollectionError("no Chrome found")

    monkeypatch.setattr(collector, "find_chrome", refuse)
    assert collector.main([]) == 1
    assert "error: no Chrome found" in capsys.readouterr().err
