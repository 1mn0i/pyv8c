"""Guard the package layering introduced by the domain-package refactor."""

import ast
from pathlib import Path

import pytest
import pyv8c


ROOT = Path(__file__).resolve().parents[2] / "pyv8c"
DOMAIN_PACKAGES = {"web/dom", "web/bom", "web/fetch", "web/network", "web/workers"}


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
    for path in (ROOT / "foundation").glob("*.py"):
        for level, module in _imports(path):
            assert not (level > 1 or module.startswith(("engine", "web", "platform"))), path


def test_domain_packages_do_not_depend_on_runtime_or_extractor_cli():
    for package in DOMAIN_PACKAGES:
        for path in (ROOT / package).glob("*.py"):
            for _level, module in _imports(path):
                assert not module.startswith("platform"), path
                assert not module.startswith("engine.extractor"), path


def test_bindings_do_not_depend_on_runtime():
    """bindings must reach realm services through core, never through Page."""
    for path in (ROOT / "engine").glob("*.py"):
        for _level, module in _imports(path):
            assert not module.startswith("platform"), path
        source = path.read_text(encoding="utf-8")
        assert "_make_promise" not in source, path
        assert "_js_error_value" not in source, path


def test_no_implicit_current_page_state_remains():
    forbidden = ("current_page", "set_current_page", "_current_page")
    for path in ROOT.rglob("*.py"):
        assert not any(token in path.read_text(encoding="utf-8") for token in forbidden), path


def test_extractor_does_not_import_browser_runtime():
    for path in (ROOT / "engine" / "extractor").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "platform.browser" not in source
        assert "from pyv8c import Browser" not in source


def test_binding_facade_uses_separate_catalog_and_schema_helpers():
    universe_source = (ROOT / "engine" / "universe.py").read_text(encoding="utf-8")
    extractor_source = (ROOT / "engine" / "extractor" / "cpp.py").read_text(encoding="utf-8")
    wrappers_source = (ROOT / "engine" / "wrappers.py").read_text(encoding="utf-8")
    assembly_source = (ROOT / "engine" / "assembly.py").read_text(encoding="utf-8")
    assert "InterfaceCatalog" in universe_source
    assert "class SignatureCache" in wrappers_source
    assert "class BindingWrapperFactory" in wrappers_source
    assert "class ClassConfigBuilder" in assembly_source
    assert "apply_template_flags" in assembly_source
    assert "from ..schema import write_baseline" in extractor_source
    assert "class InterfaceCatalog" in (ROOT / "engine" / "catalog.py").read_text(encoding="utf-8")
    assert "class StubFactory" in (ROOT / "engine" / "stubs.py").read_text(encoding="utf-8")
    assert "class BindingSession" in (ROOT / "engine" / "sessions.py").read_text(encoding="utf-8")


def test_universe_stays_a_composition_root():
    """Stub/wrapper/assembly/session bodies must not creep back into universe.py."""
    universe_source = (ROOT / "engine" / "universe.py").read_text(encoding="utf-8")
    for token in (
        "_stub_method", "_member_configs", "_class_config_for", "_declared_handlers",
        "class StubFactory", "class BindingWrapperFactory", "class ClassConfigBuilder",
        "class BindingSession", "def _stub_", "mark_direct",
    ):
        assert token not in universe_source, token


def test_universe_collaborators_use_public_surface_only():
    """stubs/wrappers/assembly/sessions must not reach into universe privates."""
    for name in ("stubs", "wrappers", "assembly", "sessions"):
        source = (ROOT / "engine" / f"{name}.py").read_text(encoding="utf-8")
        assert "self.universe._" not in source, name


def test_layering_has_no_upward_imports():
    forbidden = {
        "foundation": ("engine", "web", "platform"),
        "engine": ("web", "platform"),
        "web": ("platform",),
    }
    for layer, names in forbidden.items():
        for path in (ROOT / layer).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in names:
                assert f"pyv8c.{name}" not in source, path
                assert f"from ..{name}" not in source, path


def test_runtime_interface_catalog_is_frozen_after_bootstrap():
    pytest.importorskip("pyv8", reason="imports platform modules bound to the native extension")
    from pyv8c.platform.bootstrap import bootstrap_interfaces

    registry = bootstrap_interfaces()
    assert registry.frozen
    assert bootstrap_interfaces() is registry


def test_page_exposes_narrow_realm_services():
    pytest.importorskip("pyv8", reason="constructs a live Browser")
    from pyv8c import Browser
    from pyv8c.foundation.realm import ContextServices, DocumentServices, RealmServices, ResourceServices, TaskServices, WorkerServices

    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        # PageServices, the composition root, carries the realm, task,
        # resource, document and worker surfaces itself...
        for protocol in (RealmServices, TaskServices, ResourceServices, DocumentServices, WorkerServices):
            assert isinstance(page.services, protocol), protocol
        # ...and context services live on the context capability object,
        # which also keeps the historical ``context_services`` spelling.
        assert isinstance(page.services.context_services, ContextServices)
        assert isinstance(page.services.realm_services, RealmServices)
        assert isinstance(page.services.task_services, TaskServices)
        assert isinstance(page.services.resource_services, ResourceServices)
        assert isinstance(page.services.document_services, DocumentServices)
        assert isinstance(page.services.worker_services, WorkerServices)
        assert page.services.network_available is False
        assert page.services.interface_materialized("Document")
    finally:
        browser.close()


def test_foundation_and_services_have_no_owner_backdoors():
    """TaskQueue must not reach the Page (or any host) through an owner field."""
    tasks_source = (ROOT / "foundation" / "tasks.py").read_text(encoding="utf-8")
    assert "self.owner" not in tasks_source
    assert "TaskHost" in tasks_source

    services_source = (ROOT / "platform" / "services.py").read_text(encoding="utf-8")
    assert "_tasks.owner" not in services_source
    assert "_host.owner" not in services_source


def test_task_queue_exposes_event_loop_and_error_contract():
    from pyv8c.foundation.tasks import TaskQueue, TaskSource

    for member in ("run_turn", "next_deadline", "drain_task_errors", "pump_until"):
        assert hasattr(TaskQueue, member), member
    assert TaskSource.PARSER.value == "parser"


def test_domain_code_uses_page_resource_ownership_api():
    for path in (ROOT / "web").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in (
            "context_owner", "network_backend", "binding_session",
            "_owned_websockets", "_owned_workers", "_owned_frames",
            "binding_configuration(", "resource_loader",
            "self._page", "self.page",
        ):
            assert token not in source, (path, token)


def test_page_services_surface_is_structurally_narrow():
    """PageServices must not grow escape hatches into platform internals."""
    pytest.importorskip("pyv8", reason="constructs a live Browser")
    from pyv8c import Browser

    source = (ROOT / "platform" / "services.py").read_text(encoding="utf-8")
    for token in ("self.context_owner", "self.network_backend", "self.binding_session", "self.page"):
        assert token not in source, token

    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        services = page.services
        for name in ("context_owner", "network_backend", "binding_session", "page", "realm", "context", "browser"):
            assert not hasattr(services, name), name
    finally:
        browser.close()


def test_page_keeps_only_user_facing_api():
    """Web-serving forwards must not creep back onto the Page facade."""
    pytest.importorskip("pyv8", reason="imports platform modules bound to the native extension")
    from pyv8c.platform.browser import Page

    for name in (
        "post_task", "schedule", "cancel", "begin_network", "complete_network",
        "begin_worker", "complete_worker", "make_promise", "js_error_value",
        "promise_resolve", "install_inline_handler", "load_resource",
        "load_resource_async", "load_iframe", "resolve_worker_source",
        "websocket", "close_websocket", "network_available", "cookie_jar",
        "context_services", "binding_services", "devtools_server",
        "worker_configuration", "create_worker_realm", "new_child_page",
        "materialize_interface", "interface_materialized", "request_navigation",
        "element_class", "_node_adopted", "_parser_script_start",
    ):
        assert not hasattr(Page, name), name


def test_web_capability_split_has_no_legacy_mixed_modules():
    assert not (ROOT / "web" / "fetch" / "backend.py").exists()
    assert not (ROOT / "web" / "fetch" / "api.py").exists()
    assert "class CookieJar" in (ROOT / "web" / "network" / "cookies.py").read_text(encoding="utf-8")
    assert "class CronetNetworkBackend" in (ROOT / "web" / "network" / "backend.py").read_text(encoding="utf-8")
    assert "class WorkerRuntime" in (ROOT / "web" / "workers" / "runtime.py").read_text(encoding="utf-8")
