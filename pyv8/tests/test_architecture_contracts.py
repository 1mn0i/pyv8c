"""Architecture checks must reject invalid edges and stale build inputs."""
import importlib.util
import runpy
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parents[1]


def setup_configuration():
    with patch("setuptools.setup") as setup:
        namespace = runpy.run_path(str(ROOT / "setup.py"))
    return namespace, setup.call_args.kwargs["ext_modules"][0]


def test_build_declares_every_header_and_macro_table():
    _, extension = setup_configuration()
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*")
                if p.suffix in {".h", ".def"}}
    assert set(extension.depends) == expected
    assert "src/interfaces/handler_table.def" in extension.depends


def test_header_content_and_toolchain_changes_invalidate_fingerprint(tmp_path, monkeypatch):
    namespace, _ = setup_configuration()
    fingerprint = namespace["PyV8BuildExt"]._build_fingerprint
    monkeypatch.setitem(fingerprint.__globals__, "PROJECT_ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    header = tmp_path / "src/contract.h"
    header.write_text("before")
    for name in ("setup.py", "pyproject.toml", "v8_build.py", "v8.lib", "icudtl.dat"):
        (tmp_path / name).write_text("fixture")
    output = tmp_path / "out"
    output.mkdir()
    (output / "args.gn").write_text("v8_enable_cppgc = true")
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
