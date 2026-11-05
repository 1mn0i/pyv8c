"""Page lifetime acceptance run, with normal chrome installation enabled."""
import argparse
import ctypes
import gc
import hashlib
import json
from pathlib import Path
import sys
import tracemalloc
import weakref

import pyv8
from pyv8c import Browser


def collect():
    for _ in range(3):
        pyv8._pyv8.low_memory_notification()
        gc.collect()


def private_bytes():
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_working", "working", "peak_paged", "paged",
                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.private
    if sys.platform.startswith("linux"):
        return sum(int(line.split()[1]) * 1024
                   for line in Path("/proc/self/smaps_rollup").read_text().splitlines()
                   if line.startswith(("Private_Clean:", "Private_Dirty:")))
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    binary = Path(pyv8._pyv8.__file__)
    report = {"python": sys.version, "binary": str(binary),
              "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "samples": []}
    browser = Browser(stub_logging="off")
    owner = browser.new_profile()
    references = {name: [] for name in ("page", "window")}
    context_ids = set()
    try:
        for _ in range(20):
            page = owner.new_page()
            page.close()
        del page
        collect()
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        report["private_bytes_baseline"] = private_bytes()
        for index in range(1, args.count + 1):
            page = owner.new_page()
            context_ids.add(id(page.v8_context))
            for name, value in (("page", page), ("window", page.window)):
                references[name].append(weakref.ref(value))
            del value
            page.close()
            del page
            if index % 100 == 0 or index == args.count:
                collect()
                retained = {name: sum(ref() is not None for ref in refs)
                            for name, refs in references.items()}
                # Native Context is GC-tracked but does not support weakrefs.
                retained["context"] = sum(type(obj) is pyv8.Context and id(obj) in context_ids
                                          for obj in gc.get_objects())
                context_ids.clear()
                for refs in references.values():
                    refs.clear()
                sample = {"created": index, "retained": retained,
                          "python_bytes_delta": tracemalloc.get_traced_memory()[0] - baseline,
                          "private_bytes": private_bytes()}
                report["samples"].append(sample)
                print(json.dumps(sample), flush=True)
                assert not any(retained.values()), sample
    finally:
        browser.close()
        tracemalloc.stop()
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
