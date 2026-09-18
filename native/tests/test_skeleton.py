"""Stage 0 acceptance: the wheel imports, the platform starts, and it stops.

These cover the contract the rest of the kernel is built on: importing the
package brings V8 up, the running V8 is the one this checkout pins, the two
compile-time identity checks are visible from Python, and a clean shutdown works
in a process that never created an isolate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import v8n

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(v8n.__file__).resolve().parent.parent


def test_import_starts_the_platform():
    assert v8n.is_initialized()


def test_version_matches_the_pinned_v8():
    pinned = (CHECKOUT_ROOT / "V8_VERSION").read_text(encoding="ascii").strip()
    assert v8n.v8_version() == pinned


def test_initialize_is_idempotent():
    v8n._v8n.initialize()
    assert v8n.is_initialized()


def test_flags_must_be_strings():
    with pytest.raises(TypeError, match=r"flags\[0\]"):
        v8n._v8n.initialize(flags=[1])


def test_flags_after_start_are_refused():
    # A host that asks for flags once V8 is running must be told they cannot be
    # applied; accepting the call silently would leave it believing they were.
    with pytest.raises(RuntimeError, match="already initialized"):
        v8n._v8n.initialize(flags=["--no-lazy"])


def test_flags_from_the_environment_are_applied_at_import():
    # 'import v8n' is the host's only chance to set flags without reaching for
    # the extension first, so the facade has to read them from its environment.
    script = (
        "import v8n\n"
        "assert v8n.is_initialized()\n"
        "print(v8n.v8_version())\n"
    )
    result = run_in_subprocess(script, extra_environment={"V8N_FLAGS": "--no-lazy"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_microtask_queues_are_reported_from_the_build():
    # This V8 build passes -DV8_CPPGC_MICROTASK_QUEUE, so the extension must
    # agree with it.  A false here would make the microtask API refuse to run.
    assert v8n.microtask_queues_supported() is True


def test_icu_digest_is_compiled_in():
    digest = v8n._v8n.icu_digest()
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)


def run_in_subprocess(script: str, extra_environment: dict[str, str] | None = None):
    """Run a snippet in its own interpreter with this checkout importable.

    shutdown() is process-wide and irreversible, and the flags a host may pass
    are only meaningful before V8 starts, so both need an interpreter of their
    own rather than disturbing the rest of the suite.
    """
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(PACKAGE_ROOT), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    if extra_environment:
        environment.update(extra_environment)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_shutdown_leaves_a_clean_process():
    result = run_in_subprocess(
        "import v8n\n"
        "assert v8n.is_initialized()\n"
        "v8n.shutdown()\n"
        "assert not v8n.is_initialized()\n"
    )
    assert result.returncode == 0, result.stderr


def test_reinitializing_after_shutdown_is_refused():
    result = run_in_subprocess(
        "import v8n\n"
        "v8n.shutdown()\n"
        "try:\n"
        "    v8n._v8n.initialize()\n"
        "except RuntimeError as error:\n"
        "    print(error)\n"
        "else:\n"
        "    raise SystemExit('initialize() after shutdown() must fail')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "shut down" in result.stdout
