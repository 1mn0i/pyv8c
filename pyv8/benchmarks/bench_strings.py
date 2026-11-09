"""Measure what a string costs when it crosses the Python/JavaScript boundary.

Four loops, one per direction and per kind of text:

* ``arg_ascii``  -- JavaScript hands Python a short ASCII string.
* ``arg_utf8``   -- the same with text that is not ASCII.
* ``ret_ascii``  -- Python hands JavaScript a short ASCII string.
* ``ret_utf8``   -- the same with text that is not ASCII.

The ASCII cases are the ones a fast path can serve, so they are also the ones a
change has to be measured against: a path that helps ASCII but costs the general
case is not a win.
"""

import statistics
import sys
import time

from pyv8c import pyv8

ASCII = "abc"
UTF8 = "h\u00e9llo \u4e16\u754c"


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

    # The argument case answers with an int, so the loop measures only the trip
    # in.  The return case takes no argument, so the loop measures only the trip
    # out.
    def take(value):
        return len(value) & 1

    def give(_value=ASCII):
        return _value

    context.expose(take=take, give_ascii=lambda: ASCII, give_utf8=lambda: UTF8)

    rows = {}
    rows["arg_ascii_x50000"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) take('" + ASCII + "');"))
    rows["arg_utf8_x50000"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) take('" + UTF8 + "');"))
    rows["ret_ascii_x50000"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) give_ascii();"))
    rows["ret_utf8_x50000"] = timed(lambda: context.exec_js(
        "for (let i = 0; i < 50000; i++) give_utf8();"))
    context.close()

    print(f"--- {label} ---")
    for key, value in rows.items():
        print(f"  {key:22} median {value[0]:9.2f} ms   min {value[1]:9.2f} ms")


if __name__ == "__main__":
    measure(sys.argv[1] if len(sys.argv) > 1 else "unnamed")
