import copy
import json
import zipfile
from pathlib import Path

import pytest
import pyv8

from pyv8c import Browser

BASELINE = Path(__file__).resolve().parents[2] / "pyv8c" / "baselines" / "interface-baseline.zip"


def _baseline_document():
    with zipfile.ZipFile(BASELINE) as archive:
        return json.loads(archive.read("interface-baseline.json"))


def _custom_baseline(path):
    document = _baseline_document()
    document["AbortController"]["parent"] = "EventTarget"
    document["AbortController"]["tag"] = "VersionedAbortController"
    document["AbortController"]["constructor"]["length"] = 2
    members = document["AbortController"]["members"]["prototype"]
    abort = next(member for member in members if member["name"] == "abort")
    members.remove(abort)
    replacement = copy.deepcopy(abort)
    replacement["name"] = "alternateAbort"
    members.append(replacement)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return document


def test_default_baseline_is_bundled_and_describes_snapshot():
    browser = Browser(stub_logging="off")
    try:
        assert browser.interface_baseline_path == "pyv8c:baselines/interface-baseline.zip"
        info = browser.interface_baseline_info
        assert info["interface_count"] > 1000
        assert len(info["sha256"]) == 64
        assert info["schema"] == 5
    finally:
        browser.close()


def test_custom_baseline_path_is_absolute_and_snapshot_is_frozen(tmp_path, monkeypatch):
    baseline = tmp_path / "custom.json"
    document = _custom_baseline(baseline)
    monkeypatch.chdir(tmp_path)
    browser = Browser(interface_baseline_path="custom.json", stub_logging="off")
    original_hash = browser.interface_baseline_info["sha256"]
    try:
        assert browser.interface_baseline_path == str(baseline.resolve())
        document["_webidl"]["snapshot"] = "changed-after-browser-creation"
        baseline.write_text(json.dumps(document), encoding="utf-8")
        assert browser.interface_baseline_info["sha256"] == original_hash
        assert "alternateAbort" in {
            member["name"]
            for member in browser.interface_universe.baseline.interfaces["AbortController"]["members"]["prototype"]
        }
        newer = Browser(interface_baseline_path=baseline, stub_logging="off")
        try:
            assert newer.interface_baseline_info["sha256"] != original_hash
        finally:
            newer.close()
    finally:
        browser.close()


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("[]", "root must be an object"),
        ("{}", "must contain _webidl metadata"),
        ('{"_webidl": {"schema": 5}, "Broken": {"parent": null}}', "not a compact schema 5"),
    ],
)
def test_invalid_baseline_is_rejected_when_browser_is_created(tmp_path, content, error):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        Browser(interface_baseline_path=path)


def test_missing_baseline_is_rejected_when_browser_is_created(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        Browser(interface_baseline_path=tmp_path / "missing.json")


def test_old_baseline_schema_is_rejected_explicitly(tmp_path):
    document = _baseline_document()
    document["_webidl"]["schema"] = 2
    path = tmp_path / "schema-2.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="expected 5"):
        Browser(interface_baseline_path=path)


def test_non_compact_baseline_is_rejected_explicitly(tmp_path):
    document = _baseline_document()
    document["_webidl"]["compact"] = False
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="not a compact schema 5"):
        Browser(interface_baseline_path=path)


def test_baseline_parent_cycle_is_rejected_when_browser_is_created(tmp_path):
    entry = lambda parent, tag: {
        "parent": parent, "members": {}, "constructor": {}, "tag": tag,
        "template": {}, "constants": [],
    }
    path = tmp_path / "cycle.json"
    path.write_text(json.dumps({
        "_webidl": {"schema": 5, "compact": True}, "First": entry("Second", "First"),
        "Second": entry("First", "Second"),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="parent cycle"):
        Browser(interface_baseline_path=path)


def test_two_browsers_keep_generated_classes_and_templates_isolated(tmp_path):
    custom = tmp_path / "custom.json"
    _custom_baseline(custom)
    default_browser = Browser(stub_logging="off")
    custom_browser = Browser(interface_baseline_path=custom, stub_logging="off")
    try:
        assert default_browser.interface_universe.class_for("AbortController") is not custom_browser.interface_universe.class_for("AbortController")
        default_page = default_browser.new_context().new_page()
        custom_page = custom_browser.new_context().new_page()
        assert default_page.evaluate("[typeof AbortController.prototype.abort, typeof AbortController.prototype.alternateAbort]") == ["function", "undefined"]
        assert custom_page.evaluate("[typeof AbortController.prototype.abort, typeof AbortController.prototype.alternateAbort]") == ["undefined", "function"]
        assert default_page.evaluate("Object.getPrototypeOf(AbortController.prototype) === Object.prototype") is True
        assert custom_page.evaluate("Object.getPrototypeOf(AbortController.prototype) === EventTarget.prototype") is True
        assert default_page.evaluate("[AbortController.length, Object.prototype.toString.call(new AbortController())]") == [0, "[object AbortController]"]
        assert custom_page.evaluate("[AbortController.length, Object.prototype.toString.call(new AbortController())]") == [2, "[object VersionedAbortController]"]

        custom_page.set_content("<main><div id='value'></div></main>")
        assert custom_page.evaluate("(() => { const node=document.querySelector('#value'); return [node instanceof Node, node instanceof Element, document instanceof Document, document.querySelector('#value') === node]; })()") == [True, True, True, True]

        default_browser.close()
        assert custom_page.evaluate("new AbortController().alternateAbort()") is pyv8.Undefined
        assert custom_page.evaluate("typeof URL === 'function'") is True
    finally:
        default_browser.close()
        custom_browser.close()


def test_baseline_signatures_do_not_control_implementation_arguments(tmp_path):
    document = _baseline_document()
    contains = next(
        member for member in document["DOMTokenList"]["members"]["prototype"]
        if member["name"] == "contains"
    )
    contains.setdefault("signature", {})["required"] = 2
    path = tmp_path / "strict-contains.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    default_browser = Browser(stub_logging="off")
    strict_browser = Browser(interface_baseline_path=path, stub_logging="off")
    try:
        default_page = default_browser.new_context().new_page()
        strict_page = strict_browser.new_context().new_page()
        default_page.set_content("<body></body>")
        strict_page.set_content("<body></body>")
        assert default_page.evaluate("document.body.classList.contains('x')") is False
        assert strict_page.evaluate("document.body.classList.contains('x')") is False
        assert strict_page.evaluate("document.body.classList.contains('x', 'ignored')") is False
    finally:
        default_browser.close()
        strict_browser.close()


def test_unresolved_signatures_do_not_prevent_structurally_valid_bindings(tmp_path):
    document = _baseline_document()
    contains = next(
        member for member in document["DOMTokenList"]["members"]["prototype"]
        if member["name"] == "contains"
    )
    contains.setdefault("signature", {})["status"] = "unresolved"
    path = tmp_path / "unresolved-real.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    browser = Browser(interface_baseline_path=path, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("document.createElement('div').classList.contains('x')") is False
    finally:
        browser.close()

    document = _baseline_document()
    abort = next(
        member for member in document["AbortController"]["members"]["prototype"]
        if member["name"] == "abort"
    )
    abort.setdefault("signature", {})["status"] = "unresolved"
    path = tmp_path / "unresolved-stub.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    browser = Browser(interface_baseline_path=path, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("new AbortController().abort(1, 2, 3)") is pyv8.Undefined
    finally:
        browser.close()


def test_worker_and_iframe_reuse_their_browser_universe(tmp_path):
    custom = tmp_path / "custom.json"
    _custom_baseline(custom)
    browser = Browser(
        interface_baseline_path=custom,
        resource_loader=lambda _url, _request: {
            "body": "", "headers": {"access-control-allow-origin": "*"},
        },
        stub_logging="off",
    )
    try:
        page = browser.new_context().new_page()
        page.set_content("<iframe src='about:blank'></iframe>", url="http://example.test/")
        page.run_until_idle(timeout=1)
        frame_page = next(iter(page._owned.frames))
        assert frame_page.context_owner.browser.interface_universe is browser.interface_universe
        assert frame_page.evaluate("[typeof AbortController.prototype.abort, typeof AbortController.prototype.alternateAbort]") == ["undefined", "function"]

        page.evaluate("""(() => {
          const source = new Blob([
            "postMessage([typeof AbortController.prototype.abort, typeof AbortController.prototype.alternateAbort]);"
          ], {type: "text/javascript"});
          const worker = new Worker(URL.createObjectURL(source));
          worker.onmessage = event => window.__baselineFromWorker = event.data;
        })()""")
        page.run_until_idle(timeout=2)
        assert page.evaluate("__baselineFromWorker") == ["undefined", "function"]
    finally:
        browser.close()
