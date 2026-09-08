"""Measure pyv8c cold startup in isolated Python processes."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys


CHILD = r'''
import json
import time
from pyv8c import Browser

started = time.perf_counter_ns()
browser = Browser(stub_logging="off")
browser_created = time.perf_counter_ns()
context = browser.new_context()
context_created = time.perf_counter_ns()
page = context.new_page()
page_created = time.perf_counter_ns()
stats = dict(browser.startup_stats)
browser.close()
closed = time.perf_counter_ns()
print(json.dumps({
    "browser_ms": (browser_created - started) / 1_000_000,
    "context_ms": (context_created - browser_created) / 1_000_000,
    "page_ms": (page_created - context_created) / 1_000_000,
    "close_ms": (closed - page_created) / 1_000_000,
    "total_ms": (closed - started) / 1_000_000,
    "startup_stats": stats,
}, separators=(",", ":")))
'''


def run_once(root: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root), env.get("PYTHONPATH", "")]))
    result = subprocess.run(
        [sys.executable, "-c", CHILD], cwd=root, env=env,
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    samples = [run_once(root) for _ in range(max(1, args.runs))]
    totals = [sample["total_ms"] for sample in samples]
    summary = {
        "runs": len(samples),
        "p50_ms": statistics.median(totals),
        "p95_ms": sorted(totals)[max(0, math.ceil(len(totals) * 0.95) - 1)],
        "min_ms": min(totals),
        "max_ms": max(totals),
        "samples": samples,
    }
    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("runs={runs} p50={p50_ms:.1f}ms p95={p95_ms:.1f}ms min={min_ms:.1f}ms max={max_ms:.1f}ms".format(**summary))


if __name__ == "__main__":
    main()
