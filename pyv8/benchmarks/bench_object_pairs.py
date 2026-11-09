"""Measure the Python-object <-> JavaScript-object pair cache.

The cache is the binding's answer to "what JavaScript object stands for this
Python one in this realm".  Its cost shows up in four shapes, and a change to it
has to move them in the right direction:

* ``wrap_hit``  -- JavaScript asks for the same Python object over and over, so
                   every call is a cache hit.
* ``wrap_new``  -- every call hands JavaScript an object it has never seen, so
                   every call publishes a new pair.
* ``js_obj``    -- Python asks for the handle of an existing pair, over and over.
* ``glob``      -- the context's own global pair, read the same way.

Run it against one build, then against another, and compare:

    python benchmarks/bench_object_pairs.py before
    python benchmarks/bench_object_pairs.py after

Each phase runs five times; the median is the number to report and the minimum
is the least noisy estimate of the work actually done.
"""

import statistics
import sys
import time

from pyv8c import pyv8


class Window:
    pass


class Payload:
    pass


def timed(call, repeats=5):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples), min(samples)


def measure(label):
    context = pyv8.Context(Window())
    payload = Payload()

    def hit():
        return payload

    def make(_index):
        return Payload()

    context.expose(hit=hit, make=make)
    context.expose(first=payload, second=payload)
    assert context.exec_js("first === second") is True

    rows = {}
    rows["wrap_hit_x200k"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 200000; i++) hit();"))
    rows["wrap_new_x50k"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) make(i);"))
    rows["js_obj_x100k"] = timed(
        lambda: [context.js_obj(payload) for _ in range(100000)])
    rows["glob_x50k"] = timed(
        lambda: [context.glob for _ in range(50000)])
    # A second pass after a collection: the pairs of the first pass are garbage
    # by now, so this is also where an entry that outlived its object would show
    # up as cost.
    pyv8._pyv8.low_memory_notification()
    rows["wrap_new_x50k_again"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) make(i);"))
    context.close()

    print(f"--- {label} ---")
    for key, value in rows.items():
        print(f"  {key:22} median {value[0]:9.2f} ms   min {value[1]:9.2f} ms")


if __name__ == "__main__":
    measure(sys.argv[1] if len(sys.argv) > 1 else "unnamed")
