"""Reproducible bridge benchmark; run from a checkout with both projects on PYTHONPATH."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def measure(operation, batches=20):
    for _ in range(5):
        operation()
    samples = []
    for _ in range(batches):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return samples


def worker():
    import pyv8
    from pyv8 import AttributeSpec, IdlSpec, MethodSpec

    class Window:
        __v8_idl_spec__ = IdlSpec(global_this=True)

    class Host:
        __v8_idl_spec__ = IdlSpec(
            attributes=(AttributeSpec(name="value", getter="get_value", setter="set_value"),),
            methods=(MethodSpec(name="method", callback="method"),),
        )
        value = 1

        def get_value(self):
            return self.value

        def set_value(self, value):
            self.value = value

        def method(self, *args):
            return args[0] if args else 1

    def callback(*args):
        return args[0] if args else 1

    def fail():
        raise pyv8.JsTypeError("benchmark")

    results = {}
    context = pyv8.Context(Window(), traceable=True, trace_callback=lambda *args: None)
    context.expose(host=Host(), callback=callback, fail=fail)
    programs = {
        **{f"callback_{n}": f"callback({','.join(['1'] * n)})" for n in (0, 1, 4, 16)},
        "method": "host.method(1,2,3,4)",
        "getter": "host.value",
        "setter": "host.value=1",
        "object_roundtrip": "callback(host)",
        "exception": "try { fail() } catch(e) {}",
    }
    try:
        for name, expression in programs.items():
            script = pyv8.Script(context, f"for(let i=0;i<2000;i++){{{expression};}};0")
            results[name] = measure(lambda: context.exec_js(script))
        context.trace = True
        script = pyv8.Script(context, "for(let i=0;i<500;i++){host.method(1)};0")
        results["trace"] = measure(lambda: context.exec_js(script))
    finally:
        context.close()

    from pyv8c import Browser
    browser = Browser(real_network=False, stub_logging="off")
    owner = browser.new_profile()
    page = owner.new_page()
    page.set_content('<div id="target"></div>')
    try:
        results["dom"] = measure(lambda: page.evaluate(
            "for(let i=0;i<500;i++){document.getElementById('target').setAttribute('data-x','1')};0"))

        def lifecycle():
            created = owner.new_page()
            created.close()

        results["page_lifecycle"] = measure(lifecycle)
    finally:
        browser.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--processes", type=int, default=5)
    args = parser.parse_args()
    if args.processes < 1:
        parser.error("processes must be positive")
    if args.worker:
        print(json.dumps(worker()))
        return
    runs = []
    for index in range(args.processes):
        result = subprocess.run([sys.executable, __file__, "--worker"], capture_output=True,
                                text=True, check=True, env=os.environ.copy())
        runs.append(json.loads(result.stdout.strip().splitlines()[-1]))
        print(f"process {index + 1}/{args.processes}", file=sys.stderr)
    import pyv8
    binary = Path(pyv8._pyv8.__file__)
    report = {"python": sys.version, "platform": platform.platform(),
              "binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[1], text=True).strip(),
              "runs_ns": runs,
              "medians_ns": {name: statistics.median(statistics.median(run[name]) for run in runs)
                             for name in runs[0]}}
    root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=Path(__file__).resolve().parent,
        text=True).strip())
    report["worktree_sha256"] = hashlib.sha256(subprocess.check_output(
        ["git", "diff", "--binary", "HEAD"], cwd=root)).hexdigest()
    if args.compare:
        before = json.loads(args.compare.read_text())
        ratios = {name: value / before["medians_ns"][name] for name, value in report["medians_ns"].items()}
        report["ratios"] = ratios
        hot = [value for name, value in ratios.items() if name not in ("trace", "page_lifecycle", "dom")]
        report["hot_geomean_ratio"] = math.exp(statistics.mean(map(math.log, hot)))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "runs_ns"}, indent=2))


if __name__ == "__main__":
    main()
