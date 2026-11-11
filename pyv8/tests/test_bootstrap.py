import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('v8_bootstrap', Path(__file__).parents[1] / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_bootstrap_reports_missing_tools_and_wrong_interpreter():
    errors = bootstrap.check(version=(3, 13), which=lambda name: None,
                             implementation='cpython')
    assert any('CPython 3.12' in error for error in errors)
    assert any('Git' in error for error in errors)
    assert any('Python was not found' in error for error in errors)


def test_bootstrap_does_not_require_distribution_idl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert bootstrap.check(version=(3, 12), which=lambda name: '/tool',
                           implementation='cpython') == []
