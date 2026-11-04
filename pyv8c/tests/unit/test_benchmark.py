"""A missing performance measurement must not be reported as a passing comparison."""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def benchmark(monkeypatch):
    monkeypatch.setattr(sys, "path", sys.path.copy())
    path = Path(__file__).resolve().parents[2] / 'tools/benchmark.py'
    spec = importlib.util.spec_from_file_location('audit_benchmark', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_unavailable_case_fails_comparison(benchmark, tmp_path, capsys):
    baseline = tmp_path / 'baseline.json'
    baseline.write_text('{"cases": {}}')
    benchmark.BASELINE_FILE = baseline
    assert benchmark.compare({'failed': (None, 'measurement failed')}) is False
    assert 'measurement failed' in capsys.readouterr().out


def test_measure_preserves_raw_samples(benchmark):
    samples = iter([3, 1, 2])
    result, error = benchmark.measure(lambda: {'median': next(samples), 'min': 0}, 3)
    assert error is None
    assert result['median'] == 2 and result['runs'] == 3
    assert [sample['median'] for sample in result['samples']] == [3, 1, 2]


def test_nonpositive_runs_are_rejected(benchmark):
    with pytest.raises(SystemExit) as error:
        benchmark.main(['--runs', '0'])
    assert error.value.code == 2


@pytest.mark.parametrize("record", [False, True])
def test_failed_measurement_never_succeeds_without_a_baseline(benchmark, tmp_path, record):
    benchmark.BASELINE_FILE = tmp_path / 'missing.json'
    def fail(): raise RuntimeError('measurement failed')
    benchmark.CASES = (('failed', fail),)
    assert benchmark.main(['--runs', '1'] + (['--record'] if record else [])) == 1


def test_changed_workload_is_not_compared_or_marked_passed(benchmark, tmp_path, capsys):
    baseline = tmp_path / 'baseline.json'
    baseline.write_text('{"cases":{"query":{"median":10,"min":10}}}')
    benchmark.BASELINE_FILE = baseline
    result = {'median':1,'min':1,'unit':'ms','detail':'new','workload':'v2'}
    assert benchmark.compare({'query':(result,None)}) is False
    assert 'incomparable workload' in capsys.readouterr().out
    assert 'workload' not in baseline.read_text()
