"""The collector: what it refuses, and how it agrees with the field table."""
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


def test_a_run_writes_the_environment_and_the_window_surface_together():
    """Both facts are read from one session, so they land in one document."""
    collected = payload()
    surface = collector.build_surface({"keys": ["a"], "own": ["a", "b"]})
    document = collector.build_profile(collected, surface, source={"browser": "Chrome/153.0.8010.52"})

    assert document["window_surface"] == {"keys": ["a"], "own_names": ["a", "b"]}
    assert document["source"]["browser"] == "Chrome/153.0.8010.52"
    assert set(document) == {"schema", "source", "window_surface", *COLLECTED_KEYS}
    # The section is not an environment key: the profile still loads as one.
    assert BrowserEnvironment.from_dict(document).to_dict() == collected


def test_the_surface_script_asks_for_what_the_section_carries():
    assert "Object.keys(window)" in collector.SURFACE_SCRIPT
    assert "Object.getOwnPropertyNames(window)" in collector.SURFACE_SCRIPT


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
