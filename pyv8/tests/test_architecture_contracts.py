"""Architecture checks must reject invalid edges and stale build inputs."""
import importlib.util
import os
import runpy
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parents[1]


def setup_configuration():
    namespace = runpy.run_path(str(ROOT / "build_support.py"))
    return namespace, namespace["ext_modules"][0]


def test_build_declares_every_header_and_macro_table():
    _, extension = setup_configuration()
    expected = {p.relative_to(ROOT.parent).as_posix() for p in (ROOT / "src").rglob("*")
                if p.suffix in {".h", ".def"}}
    assert set(extension.depends) == expected
    assert "pyv8/src/interfaces/handler_table.def" in extension.depends


def test_header_content_and_toolchain_changes_invalidate_fingerprint(tmp_path, monkeypatch):
    namespace, _ = setup_configuration()
    fingerprint = namespace["PyV8BuildExt"]._build_fingerprint
    (tmp_path / 'setup.py').write_text('fixture')
    (tmp_path / 'pyproject.toml').write_text('fixture')
    tmp_path = tmp_path / 'pyv8'
    tmp_path.mkdir()
    monkeypatch.setitem(fingerprint.__globals__, "PROJECT_ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    header = tmp_path / "src/contract.h"
    header.write_text("before")
    for name in ("build_support.py", "v8_build.py", "v8.lib", "icudtl.dat"):
        (tmp_path / name).write_text("fixture")
    output = tmp_path / "out"
    output.mkdir()
    (output / "args.gn").write_text("v8_enable_cppgc = true")
    monkeypatch.setenv("PYV8_CPPGC_MICROTASK_QUEUE", "0")
    config = SimpleNamespace(library_file=tmp_path / "v8.lib", version="15.2.20.1",
                             v8_home=tmp_path, output_dir=output, gn_args={},
                             icu_data_file=lambda: tmp_path / "icudtl.dat")
    before = fingerprint(config)
    assert fingerprint(config) == before
    header.write_text("after")
    assert fingerprint(config) != before
    unchanged = fingerprint(config)
    monkeypatch.setenv("CFLAGS", "/DARCHITECTURE_CHECK")
    assert fingerprint(config) != unchanged
    include = tmp_path / 'include'
    include.mkdir()
    v8_header = include / 'v8.h'
    v8_header.write_text('first')
    unchanged = fingerprint(config)
    v8_header.write_text('other')
    assert fingerprint(config) != unchanged
    obj = output / 'obj'
    obj.mkdir()
    archive = obj / 'temporal.rlib'
    archive.write_bytes(b'first')
    (obj / 'mksnapshot.ninja').write_text('build ./mksnapshot: link obj/temporal.rlib\n')
    unchanged = fingerprint(config)
    archive.write_bytes(b'other')
    assert fingerprint(config) != unchanged
    library = tmp_path / 'v8.lib'
    before_stat = library.stat()
    unchanged = fingerprint(config)
    library.write_bytes(b'changed')
    os.utime(library, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    assert fingerprint(config) != unchanged


@pytest.mark.parametrize('target', ['mksnapshot', 'mksnapshot.exe'])
def test_snapshot_link_inputs_support_platform_targets(tmp_path, target):
    namespace, _ = setup_configuration()
    obj = tmp_path / 'obj'
    obj.mkdir()
    (obj / 'temporal.rlib').write_bytes(b'archive')
    (obj / 'mksnapshot.ninja').write_text(f'build ./{target}: link obj/temporal.rlib\n')
    assert namespace['_snapshot_link_inputs'](tmp_path) == [(obj / 'temporal.rlib').resolve()]


def test_snapshot_link_inputs_reject_missing_archive(tmp_path):
    namespace, _ = setup_configuration()
    obj = tmp_path / 'obj'
    obj.mkdir()
    (obj / 'mksnapshot.ninja').write_text('build ./mksnapshot: link obj/missing.rlib\n')
    with pytest.raises(namespace['V8BuildError'], match='link input was not found'):
        namespace['_snapshot_link_inputs'](tmp_path)


def test_snapshot_link_inputs_handle_ninja_continuation_and_order(tmp_path):
    namespace, _ = setup_configuration()
    obj = tmp_path / 'obj'
    obj.mkdir()
    (obj / 'clang_rt.builtins.lib').write_bytes(b'builtins')
    (obj / 'temporal library.rlib').write_bytes(b'archive')
    (obj / 'mksnapshot.ninja').write_text(
        'build ./mksnapshot.exe: link obj/clang_rt.builtins.lib $\n'
        '    obj/temporal$ library.rlib\n')
    assert namespace['_snapshot_link_inputs'](tmp_path) == [
        (obj / 'temporal library.rlib').resolve(), (obj / 'clang_rt.builtins.lib').resolve()]


@pytest.mark.parametrize("source,target", [
    ("pyv8.h", "pyclass.h"),
    ("isolate/context.cpp", "pyfunction.h"),
    ("values/jsvalue.cpp", "pyv8.h"),
])
def test_composition_does_not_exempt_new_dependencies(tmp_path, monkeypatch, source, target):
    spec = importlib.util.spec_from_file_location("strict_layout", ROOT / "tools/check_layout.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    copied = tmp_path / "src"
    shutil.copytree(ROOT / "src", copied)
    path = copied / source
    path.write_text(path.read_text(encoding="utf-8") + f'\n#include "{target}"\n', encoding="utf-8")
    monkeypatch.setattr(checker, "SOURCE_ROOT", copied)
    monkeypatch.setattr(sys, "argv", ["check_layout.py", "--enforce"])
    assert checker.main() == 1
