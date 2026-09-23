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


def test_the_default_file_name_names_the_browser(tmp_path):
    source = {"browser": "Chrome/153.0.8010.52", "platform": "win32"}
    assert collector.default_name(source) == "chrome-153.0.8010.52-win32.json"
    assert collector.default_name({}) == "chrome-unknown-unknown.json"


def test_a_failed_collection_exits_nonzero_with_the_reason(monkeypatch, capsys):
    def refuse(explicit=None):
        raise collector.CollectionError("no Chrome found")

    monkeypatch.setattr(collector, "find_chrome", refuse)
    assert collector.main([]) == 1
    assert "error: no Chrome found" in capsys.readouterr().err
