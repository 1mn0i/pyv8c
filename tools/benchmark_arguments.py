"""Repeatable argument-call benchmark; run unchanged against before/after PYTHONPATHs."""

import argparse
import json
import statistics
import time


def main():
    from pyv8c import Browser

    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--loops", type=int, default=10000)
    args = parser.parse_args()
    records = {"browser_init_ms": [], "page_init_ms": [], "dom_ms": [], "storage_ms": []}
    scripts = {
        "dom_ms": "(()=>{const node=document.createElement('div');for(let index=0;index<LOOPS;index++){node.setAttribute('x','42');node.getAttribute('x')}})()",
        "storage_ms": "(()=>{for(let index=0;index<LOOPS;index++){localStorage.setItem('x','42');localStorage.getItem('x')}})()",
    }
    for sample in range(args.samples + 2):
        started = time.perf_counter_ns()
        browser = Browser(stub_logging="off")
        browser_created = time.perf_counter_ns()
        try:
            page = browser.new_context().new_page()
            page_created = time.perf_counter_ns()
            measured = {
                "browser_init_ms": (browser_created - started) / 1e6,
                "page_init_ms": (page_created - browser_created) / 1e6,
            }
            for name, source in scripts.items():
                page.evaluate(source.replace("LOOPS", "1000"))
                script = source.replace("LOOPS", str(args.loops))
                started = time.perf_counter_ns()
                page.evaluate(script)
                measured[name] = (time.perf_counter_ns() - started) / 1e6
            if sample >= 2:
                for name, value in measured.items():
                    records[name].append(value)
        finally:
            browser.close()
    print(
        json.dumps(
            {
                "samples": args.samples,
                "loops": args.loops,
                "timings": {
                    name: {"median": statistics.median(values), "min": min(values), "max": max(values), "raw": values}
                    for name, values in records.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
