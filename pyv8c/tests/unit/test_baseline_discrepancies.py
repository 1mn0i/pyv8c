"""Tolerated baseline differences are countable, not just logged once."""
import pytest

pytest.importorskip("pyv8c.pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.idl import Idl  # noqa: E402
from pyv8c.engine.bindings import Bindings  # noqa: E402
from pyv8c.platform.bootstrap import bootstrap_runtime  # noqa: E402


def _bindings(mismatch):
    return Bindings(
        Idl.load(), runtime=bootstrap_runtime(),
        stub_logging="off", baseline_mismatch=mismatch,
    )


def test_a_tolerated_mismatch_is_counted_once_and_error_mode_counts_none():
    tolerant = _bindings("warn")
    assert tolerant.discrepancies == ()
    assert tolerant.report_mismatch("Window.thing is not present") is True
    assert tolerant.report_mismatch("Window.thing is not present") is True
    assert tolerant.discrepancies == ("Window.thing is not present",)
    assert _bindings("error").report_mismatch("Window.thing is not present") is False


def test_baseline_warning_is_independent_of_stub_logging(monkeypatch):
    warnings = []
    monkeypatch.setattr("pyv8c.engine.bindings.logger.warning", lambda *args: warnings.append(args))
    bindings = _bindings("warn")
    bindings.report_mismatch("a baseline mismatch")
    bindings.report_mismatch("a baseline mismatch")
    assert len(warnings) == 1
    assert warnings[0][-1] == "a baseline mismatch"


def test_no_hand_written_declaration_contradicts_the_baseline():
    """Compiling every interface reports no tolerated difference at all."""
    bindings = _bindings("warn")
    bindings.validate()
    assert bindings.discrepancies == ()
