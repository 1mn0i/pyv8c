"""Measure what a Python collection costs when it crosses into JavaScript.

Every row below builds a real JavaScript array or object up front, element by
element, whether or not the script that receives it ever looks inside:

* ``to_js``         -- the explicit conversion, driven from Python.
* ``ret_list_len``  -- a Python getter returns 1000 items and the script reads only
                       ``length``.  This is the part a lazy array could skip.
* ``ret_list_iter`` -- the same list, read to the end.  This is the part nothing can
                       skip, and what a lazy array would add its own overhead to.
* ``ret_dict``      -- 1000 keys, of which the script reads only the count.
* ``ret_nested``    -- 1000 small objects, the shape a JSON body arrives in.

The pair of list rows is the one that decides whether laziness pays: a script that
takes a list and only measures it is work that could be deferred, and a script that
walks it is work that cannot.

Run it against one build, then against another:

    python benchmarks/bench_collections.py before
    python benchmarks/bench_collections.py after
"""

import statistics
import sys
import time

import pyv8

ITEMS = 1000
ROUNDS = 100


class Window:
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
    items = list(range(ITEMS))
    mapping = {f"k{index}": index for index in range(ITEMS)}
    nested = [{"a": index, "b": str(index)} for index in range(ITEMS)]

    context.expose(
        give_list=lambda: items,
        give_dict=lambda: mapping,
        give_nested=lambda: nested,
    )

    rows = {}
    rows["to_js_list_x100"] = timed(
        lambda: [context.to_js(items) for _ in range(ROUNDS)]
    )
    rows["ret_list_len_x100"] = timed(
        lambda: context.exec_js(
            f"(() => {{ let n = 0; for (let i = 0; i < {ROUNDS}; i++)"
            f" n += give_list().length; return n; }})()"
        )
    )
    rows["ret_list_iter_x100"] = timed(
        lambda: context.exec_js(
            f"(() => {{ let n = 0; for (let i = 0; i < {ROUNDS}; i++) {{"
            f" const a = give_list(); for (let j = 0; j < a.length; j++) n += a[j]; }}"
            f" return n; }})()"
        )
    )
    rows["ret_dict_x100"] = timed(
        lambda: context.exec_js(
            f"(() => {{ let n = 0; for (let i = 0; i < {ROUNDS}; i++)"
            f" n += Object.keys(give_dict()).length; return n; }})()"
        )
    )
    rows["ret_nested_x100"] = timed(
        lambda: context.exec_js(
            f"(() => {{ let n = 0; for (let i = 0; i < {ROUNDS}; i++)"
            f" n += give_nested().length; return n; }})()"
        )
    )
    context.close()

    print(f"--- {label} ---")
    for key, value in rows.items():
        print(f"  {key:22} median {value[0]:9.2f} ms   min {value[1]:9.2f} ms")
    return rows


if __name__ == "__main__":
    measure(sys.argv[1] if len(sys.argv) > 1 else "unnamed")
