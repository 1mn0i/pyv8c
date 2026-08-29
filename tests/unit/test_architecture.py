"""Guard the package layering introduced by the domain-package refactor."""

import ast
from pathlib import Path

import pyv8c


ROOT = Path(__file__).resolve().parents[2] / "pyv8c"
DOMAIN_PACKAGES = {"dom", "bom", "network", "workers"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            yield node.level, node.module or ""


def test_public_top_level_api_is_available():
    expected = {
        "Browser", "BrowserContext", "Page", "Document", "Window", "URL",
        "Blob", "Request", "Response", "XMLHttpRequest", "Worker", "DOMException",
    }
    assert expected.issubset(set(pyv8c.__all__))
    # The root must remain importable for the standalone Chromium extractor;
    # resolving browser classes themselves requires the optional native pyv8.
    assert callable(pyv8c.__getattr__)


def test_core_has_no_relative_dependency_on_higher_layers():
    for path in (ROOT / "core").glob("*.py"):
        for level, module in _imports(path):
            assert not (level > 1 or module.startswith(("bindings", "dom", "bom", "network", "workers", "runtime"))), path


def test_domain_packages_do_not_depend_on_runtime_or_extractor_cli():
    for package in DOMAIN_PACKAGES:
        for path in (ROOT / package).glob("*.py"):
            for _level, module in _imports(path):
                assert not module.startswith("runtime"), path
                assert not module.startswith("bindings.extractor"), path


def test_no_implicit_current_page_state_remains():
    forbidden = ("current_page", "set_current_page", "_current_page")
    for path in ROOT.rglob("*.py"):
        assert not any(token in path.read_text(encoding="utf-8") for token in forbidden), path


def test_extractor_does_not_import_browser_runtime():
    for path in (ROOT / "bindings" / "extractor").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "runtime.browser" not in source
        assert "from pyv8c import Browser" not in source
