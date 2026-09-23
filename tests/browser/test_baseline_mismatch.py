"""Running against a baseline from another Chromium version.

``baseline_mismatch="warn"`` (the default) reports a difference once and skips
what the baseline does not describe; ``"error"`` refuses it instead.
"""
import json
import zipfile
from pathlib import Path

import pytest
from loguru import logger

from pyv8c import Browser

BASELINE = Path(__file__).resolve().parents[2] / "pyv8c" / "baselines" / "interface-baseline.zip"

#: A member the interface declares and a 145 baseline does not have.
EXTRA_MEMBER = ("EventTarget", "onbeforefilter")


def baseline_without_member(tmp_path):
    """The bundled baseline with one member dropped everywhere, as an older one would be."""
    path = tmp_path / "interface-baseline.zip"
    with zipfile.ZipFile(BASELINE) as source:
        document = json.loads(source.read("interface-baseline.json"))
    for interface in document.values():
        members = interface.get("members") if isinstance(interface, dict) else None
        if not members:
            continue
        for location in ("instance", "prototype", "interface"):
            members[location] = [
                entry for entry in members.get(location, ())
                if entry.get("name") != EXTRA_MEMBER[1]
            ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("interface-baseline.json", json.dumps(document))
    return path


def test_a_missing_member_stops_the_page_in_error_mode(tmp_path):
    browser = Browser(
        interface_baseline_path=baseline_without_member(tmp_path),
        baseline_mismatch="error", stub_logging="off",
    )
    try:
        with pytest.raises(TypeError, match="onbeforefilter"):
            browser.new_context().new_page()
    finally:
        browser.close()


def test_warn_mode_skips_the_member_instead(tmp_path):
    browser = Browser(
        interface_baseline_path=baseline_without_member(tmp_path),
        baseline_mismatch="warn", stub_logging="off",
    )
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://warn.test/")
        # The member is not in this baseline, so it is not installed at all.
        assert page.evaluate("typeof EventTarget.prototype.onbeforefilter") == "undefined"
    finally:
        browser.close()


def test_warn_mode_reports_the_mismatch_once(tmp_path):
    messages = []
    token = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        browser = Browser(
            interface_baseline_path=baseline_without_member(tmp_path),
            baseline_mismatch="warn", stub_logging="on",
        )
        try:
            browser.new_context().new_page()
        finally:
            browser.close()
        reported = [message for message in messages
                    if "onbeforefilter" in message and "interface baseline" in message]
        assert len(reported) == 1, reported
    finally:
        logger.remove(token)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="baseline_mismatch"):
        Browser(baseline_mismatch="quiet")
