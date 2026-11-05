"""Exercise diagnostic hooks against a deterministic host-served local page."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pyv8c
import pytest
from pyv8c.platform.resources.documents import DocumentResourceController
from pyv8c.platform.resources.frames import FrameResourceController


def test_frame_and_style_trace_hooks_restore_their_methods(monkeypatch, capsys):
    path = Path(__file__).parents[3] / "examples/px3/script/trace.py"
    if not path.is_file():
        pytest.skip("optional local PX3 trace example is not present")
    monkeypatch.setitem(__import__("sys").modules, "common", SimpleNamespace(
        URL="http://trace.test/", here=None, launch=None, chrome_page=None,
        snapshot=lambda page, script: page.evaluate(script), there=None))
    spec = importlib.util.spec_from_file_location("resource_trace_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_browser = pyv8c.Browser
    def loader(url, request):
        if url.endswith("sheet.css"):
            return {"body": "body { color: red }", "headers": {"content-type": "text/css"}}
        return {"body": '<link rel="stylesheet" href="/sheet.css"><iframe srcdoc="child"></iframe>',
                "headers": {"content-type": "text/html"}}
    monkeypatch.setattr(pyv8c, "Browser", lambda **options: original_browser(
        real_network=False, resource_loader=loader, **options))
    ensure = FrameResourceController.ensure_iframe_context
    complete = DocumentResourceController.complete_document_resource
    module.trace_frames(0)
    module.trace_style(0)
    assert FrameResourceController.ensure_iframe_context is ensure
    assert DocumentResourceController.complete_document_resource is complete
    output = capsys.readouterr().out
    assert "ensure" in output and "complete" in output and "sheet.css" in output
