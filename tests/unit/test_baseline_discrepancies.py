"""Tolerated baseline differences are countable, not just logged once."""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.baseline import BaselineRepository  # noqa: E402
from pyv8c.engine.universe import InterfaceUniverse  # noqa: E402
from pyv8c.platform.bootstrap import bootstrap_runtime  # noqa: E402


def _universe(mismatch):
    return InterfaceUniverse(
        BaselineRepository.load(), runtime=bootstrap_runtime(),
        stub_logging="off", baseline_mismatch=mismatch,
    )


def test_a_tolerated_mismatch_is_counted_once_and_error_mode_counts_none():
    tolerant = _universe("warn")
    assert tolerant.discrepancies == ()
    assert tolerant.report_mismatch("Window.thing is not present") is True
    assert tolerant.report_mismatch("Window.thing is not present") is True
    assert tolerant.discrepancies == ("Window.thing is not present",)
    assert _universe("error").report_mismatch("Window.thing is not present") is False


def test_no_hand_written_declaration_contradicts_the_baseline():
    """Compiling every interface reports nothing: a declared member the baseline does
    not carry (a method written as an attribute, a dropped interface member) is a bug
    in one of the two, and this is where it has to show."""
    universe = _universe("warn")
    universe.validate()
    assert universe.discrepancies == ()
