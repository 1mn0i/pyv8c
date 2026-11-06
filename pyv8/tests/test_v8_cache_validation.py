import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / 'tools' / 'validate_v8_cache.py'
    spec = importlib.util.spec_from_file_location('pyv8_cache_validation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_must_include_link_inputs_and_compilers(tmp_path, monkeypatch):
    module = _module()
    home = tmp_path / 'v8'
    output = home / 'out'
    llvm = home / 'third_party/llvm-build/Release+Asserts/bin'
    llvm.mkdir(parents=True)
    (llvm / 'clang-cl.exe').touch()
    (llvm / 'lld-link.exe').touch()
    obj = output / 'obj'
    obj.mkdir(parents=True)
    (obj / 'mksnapshot.ninja').write_text('build ./mksnapshot.exe: link obj/temporal.rlib\n')
    archive = obj / 'temporal.rlib'
    archive.touch()
    config = SimpleNamespace(v8_home=home, output_dir=output, _can_reuse_build=lambda: True)
    monkeypatch.setattr(module, 'cppgc_microtask_queue_enabled', lambda path: True)
    module.validate_cache(config)
    archive.unlink()
    with pytest.raises(module.V8BuildError, match='link input was not found'):
        module.validate_cache(config)


def test_cache_requires_matching_build_stamp():
    module = _module()
    config = SimpleNamespace(_can_reuse_build=lambda: False)
    with pytest.raises(module.V8BuildError, match='stamp'):
        module.validate_cache(config)
