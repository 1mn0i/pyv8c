import json
from pathlib import Path
import shutil
import sys

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_browser_interfaces as extractor


def test_visible_browser_arguments_never_enable_headless(tmp_path):
    args = extractor.browser_args("chrome.exe", 9222, tmp_path)
    assert "--headless" not in args
    assert any(item.startswith("--remote-debugging-port=") for item in args)
    assert any(item.startswith("--user-data-dir=") for item in args)


def test_visible_browser_cleanup_removes_profile_and_stops_process():
    browser = extractor.VisibleBrowser("chrome.exe")
    profile = browser.profile

    class Process:
        returncode = None
        def terminate(self): self.terminated = True
        def wait(self, timeout): self.waited = timeout

    process = Process()
    browser.process = process
    browser.__exit__(None, None, None)
    assert not profile.exists()
    assert process.terminated and process.waited == 5


def test_reflection_merge_exposure_and_schema3(tmp_path):
    window = [{
        "name": "URL", "parent": None, "constructible": True,
        "constructor": {"allow_new": True}, "exposed_in": ["Window"],
        "length": 1, "tag": "URL",
        "members": {"instance": [], "interface": [], "prototype": [
            {"name": "toString", "kind": "operation", "length": 0},
        ]},
    }]
    worker = [{
        "name": "URL", "parent": None, "constructible": True,
        "constructor": {"allow_new": True}, "exposed_in": ["Worker"],
        "length": 1, "tag": "URL",
        "members": {"instance": [], "interface": [], "prototype": [
            {"name": "parse", "kind": "operation", "length": 2},
        ]},
    }]
    output = tmp_path / "baseline.json"
    baseline = extractor.build_baseline(
        window, worker, browser={"Browser": "Chrome/123.0"},
        signature_path=TOOLS / "runtime-signatures.json",
        reference_path=Path(__file__).resolve().parents[1] / "pyv8c" / "baselines" / "interface-baseline.json",
    )
    output.write_text(json.dumps(baseline), encoding="utf-8")
    assert baseline["_webidl"]["schema"] == 3
    assert baseline["URL"]["exposed_in"] == ["Window", "Worker"]
    assert {m["name"] for m in baseline["URL"]["members"]["prototype"]} >= {"toString", "parse"}
    assert baseline["URL"]["constructor"]["signature"]["status"] in {"generated", "manual-reviewed"}


def test_runtime_signature_table_covers_core_implementations():
    table = json.loads((TOOLS / "runtime-signatures.json").read_text(encoding="utf-8"))
    for name in ("URL", "Element", "Headers", "Blob", "Request", "Response"):
        assert name in table["constructors"]
    for key in ("URL.toString", "Element.getAttribute", "Headers.get", "Blob.slice",
                "Response.text", "Response.json"):
        assert key in table["members"]


@pytest.mark.integration
def test_real_visible_browser_collection_and_cleanup(tmp_path):
    executable = next((Path(item) for item in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ) if Path(item).is_file()), None)
    if executable is None or extractor.websocket_connect is None:
        pytest.skip("Chrome/Edge executable or websockets package is unavailable")
    output = tmp_path / "browser.json"
    baseline = extractor.collect(
        str(executable), output,
        signature_path=TOOLS / "runtime-signatures.json",
        reference_path=Path(__file__).resolve().parents[1] / "pyv8c" / "baselines" / "interface-baseline.json",
    )
    assert output.is_file()
    assert baseline["_webidl"]["generator"] == "browser-cdp"
    assert baseline["URL"]["source"] == "browser-reflection"
