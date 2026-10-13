"""Architecture enforcement recognizes macro includes and rejects upward edges."""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_layout_includes_macro_definition_files():
    spec = importlib.util.spec_from_file_location("source_layout", ROOT / "tools/check_layout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert any(path.name == "handler_table.def" for path in module.source_files())


def test_source_layout_is_enforced():
    result = subprocess.run([sys.executable, str(ROOT / "tools/check_layout.py"), "--enforce"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
