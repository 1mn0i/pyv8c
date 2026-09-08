"""Measure binding workloads in independent processes; report raw samples and quantiles."""

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def sample():
    from pyv8c import Browser

    browser = Browser(stub_logging="off")
    owner = browser.new_context()
    page = owner.new_page()
    universe = browser.interface_universe
    timings = {}
    initial_compiles = len(universe._assembly._compiled)
    initial_signatures = len(universe._wrappers._parameter_cache._values)
    try:
        started = time.perf_counter_ns()
        for _ in range(5):
            extra = owner.new_page()
            extra.close()
        timings["warm_page_ms"] = (time.perf_counter_ns() - started) / 5_000_000
        warm_compiles = len(universe._assembly._compiled) - initial_compiles
        warm_signatures = len(universe._wrappers._parameter_cache._values) - initial_signatures
        started = time.perf_counter_ns()
        page.evaluate("typeof CSSStyleDeclaration")
        timings["lazy_first_touch_ms"] = (time.perf_counter_ns() - started) / 1_000_000
        names = ["Text", "Comment", "DocumentFragment", "Attr", "Range"]
        started = time.perf_counter_ns()
        page.realm.executor.call(lambda: page.realm.binding_session._materialize(names))
        timings["batch_config_ms"] = (time.perf_counter_ns() - started) / 1_000_000
        started = time.perf_counter_ns()
        page.evaluate("void [" + ",".join(names) + "]")
        timings["batch_template_ms"] = (time.perf_counter_ns() - started) / 1_000_000
        page.evaluate("globalThis.benchNode = document.createElement('div')")
        script = "for(let index=0;index<10000;index++){benchNode.setAttribute('data-x','value');benchNode.getAttribute('data-x');}"
        page.evaluate(script)
        started = time.perf_counter_ns()
        page.evaluate(script)
        timings["dom_20000_calls_ms"] = (time.perf_counter_ns() - started) / 1_000_000
        counts = {
            "initial_compiled_interfaces": initial_compiles,
            "initial_signature_cache_entries": initial_signatures,
            "warm_extra_compiles": warm_compiles,
            "warm_extra_signature_entries": warm_signatures,
            "compiled_interfaces": len(universe._assembly._compiled),
            "signature_cache_entries": len(universe._wrappers._parameter_cache._values),
        }
        return {"timings": timings, "counts": counts}
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        print(json.dumps(sample()))
        return
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
    samples = []
    for _ in range(max(1, args.runs)):
        result = subprocess.run(
            [sys.executable, __file__, "--child"], cwd=root, env=env, check=True, capture_output=True, text=True
        )
        samples.append(json.loads(result.stdout.strip().splitlines()[-1]))
    summary = {}
    for name in samples[0]["timings"]:
        values = sorted(item["timings"][name] for item in samples)
        summary[name] = {"p50": statistics.median(values), "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)]}
    print(json.dumps({"runs": len(samples), "summary": summary, "samples": samples}, indent=2))


if __name__ == "__main__":
    main()
