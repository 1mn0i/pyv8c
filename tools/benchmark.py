"""Measured numbers for the paths a host pays for, before and after a change.

    python tools/benchmark.py --record     # record tools/perf_baseline.json
    python tools/benchmark.py              # compare the current run against it

Every case reports the median and the minimum of its runs, in fresh Browser instances and
without the network, so compare a machine against itself.  ``host_call`` minus ``js_loop``
is what crossing into Python costs, and ``evaluate`` is the floor both of them pay.
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import platform
import subprocess
import json
import statistics
import sys
import time
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE_FILE = Path(__file__).resolve().parent / "perf_baseline.json"
for _path in (REPO, REPO.parent / "_pyv8"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_policy import measure_startup  # noqa: E402  the gate's own startup measurement

#: A regression has to move the median this much, and the minimum too.
TOLERANCE = 0.10

#: Chosen so a case takes tens of milliseconds and the run stays under a minute.
DOM_NODES = 4000
PARSE_NODES = 4000
QUERY_REPEATS = 200
DISPATCH_REPEATS = 20000
HOST_CALL_REPEATS = 20000
MEMBER_CALL_REPEATS = 20000
JS_LOOP_REPEATS = 20000
EVALUATE_REPEATS = 5000
PAGE_CYCLES = 15
RESOURCE_COUNT = 200
RESIDENT_PAGES = 8


def _document(nodes):
    rows = "".join(f'<div class="row" id="r{i}"><span>row {i}</span></div>' for i in range(nodes))
    return f"<!DOCTYPE html><html><body><div id='root'>{rows}</div></body></html>"


def _loader(url, request):
    from pyv8c import NetworkResponse
    return NetworkResponse(url=url, status=200, body=b"x" * 64,
                           headers={"content-type": "image/gif"})


def _browser(**kwargs):
    from pyv8c import Browser
    return Browser(stub_logging="off", **kwargs)


def _page(browser, markup="<body></body>"):
    page = browser.new_profile().new_page()
    page.set_content(markup)
    return page


def _seconds_per(operation, repeats):
    """Seconds one repeat of *operation* takes."""
    started = time.perf_counter()
    operation()
    return (time.perf_counter() - started) / repeats


def _js_seconds(page, script):
    """Seconds one run of the JavaScript *script* takes, round trip included."""
    return _seconds_per(lambda: page.evaluate(f"(() => {{ {script} }})()"), 1)


def _committed_bytes():
    """This process's committed memory, in bytes.

    The working set is what the machine has actually touched, and a V8 realm that
    reserves pages without reading them does not move it, which is why the first
    version of this case measured zero.  psutil answers when it is installed; the
    native call is the fallback, and it raises rather than report a false zero.
    """
    try:
        import psutil
        return float(psutil.Process().memory_info().private)
    except ImportError:
        pass
    if sys.platform == "win32":
        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        if not ok:
            raise OSError("GetProcessMemoryInfo failed")
        return float(counters.PagefileUsage)
    import resource
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024.0


def _sample(value, unit, detail):
    return {"median": float(value), "min": float(value), "unit": unit, "detail": detail}


def case_startup():
    """Browser -> profile -> page -> set_content, measured the way the gate measures it."""
    minimum, median, modules = measure_startup(runs=3)
    if median is None:
        raise RuntimeError(f"startup could not be measured: {modules}")
    return {"median": median, "min": minimum, "unit": "ms",
            "detail": f"{modules} modules imported"}


def case_cold_compile():
    """Compiling every interface with every process cache cleared: what a first page pays."""
    from pyv8c.engine import binder
    from pyv8c.engine import idl as idl_module
    from pyv8c.engine import bindings as bindings_module
    from pyv8c.engine.idl import Idl
    from pyv8c.engine.bindings import Bindings
    from pyv8c.platform.bootstrap import bootstrap_runtime

    # Every one of these answers a later page in milliseconds, so a warm compile measures the
    # cache rather than the work; clearing them is what a fresh process does anyway.
    binder._COMPILED.clear()
    binder._PARAMETERS.clear()
    bindings_module._GENERATED.clear()
    idl_module._DOCUMENTS.clear()
    idl_module._VIEWS.clear()
    bindings = Bindings(Idl.load(), runtime=bootstrap_runtime(),
                                 stub_logging="off")
    names = bindings.idl.names()
    started = time.perf_counter()
    for name in names:
        bindings.class_config_for(name)
    return _sample((time.perf_counter() - started) * 1000, "ms",
                   f"{len(names)} interfaces compiled cold")


def case_page_cycle():
    """Opening and closing a page: realm assembly and teardown."""
    browser = _browser()
    profile = browser.new_profile()
    try:
        def operation():
            for _ in range(PAGE_CYCLES):
                profile.new_page().close()
        return _sample(_seconds_per(operation, PAGE_CYCLES) * 1000, "ms/op",
                       f"open then close, {PAGE_CYCLES} cycles")
    finally:
        browser.close()


def case_evaluate():
    """An empty host round trip: page.evaluate returning a primitive."""
    browser = _browser()
    page = _page(browser)
    try:
        def operation():
            for _ in range(EVALUATE_REPEATS):
                page.evaluate("1")
        return _sample(_seconds_per(operation, EVALUATE_REPEATS) * 1000, "ms/op",
                       "evaluate('1')")
    finally:
        browser.close()


def case_host_call():
    """JavaScript calling into Python: setAttribute in a loop."""
    browser = _browser()
    page = _page(browser)
    try:
        elapsed = _js_seconds(page, (
            "const el = document.createElement('div');"
            f" for (let i = 0; i < {HOST_CALL_REPEATS}; i++) el.setAttribute('data-x', '1');"
        ))
        return _sample(elapsed * 1000, "ms", f"{HOST_CALL_REPEATS} setAttribute calls")
    finally:
        browser.close()


def case_js_loop():
    """The same loop with no host call in it: the floor for the case above."""
    browser = _browser()
    page = _page(browser)
    try:
        elapsed = _js_seconds(page, (
            "let total = 0;"
            f" for (let i = 0; i < {JS_LOOP_REPEATS}; i++) total += i;"
        ))
        return _sample(elapsed * 1000, "ms", f"{JS_LOOP_REPEATS} additions, no host call")
    finally:
        browser.close()


def _member_call_seconds(browser, *, expect_aborted):
    """Seconds the same member call takes, however that member is answered."""
    page = _page(browser)
    try:
        actual = page.evaluate('(() => { const c = new AbortController(); c.abort(); return c.signal.aborted; })()')
        if actual is not expect_aborted:
            raise RuntimeError('member benchmark implementation does not match its workload')
        elapsed = _js_seconds(page, (
            f"for (let i = 0; i < {MEMBER_CALL_REPEATS}; i++) new AbortController().abort();"
        ))
        return elapsed * 1000
    finally:
        browser.close()


def case_stub_call():
    """The floor a host hook is held to: the same member call, answered by its stub."""
    from pyv8c.engine.binder import _Stubs
    callback = _Stubs('off').method('AbortController', 'abort')
    if not getattr(callback, '__v8_idl_stub__', False):
        raise RuntimeError('benchmark callback is not a stub')
    result = _sample(_member_call_seconds(_browser(interface_overrides={
        'AbortController': {'abort': callback}}), expect_aborted=False), 'ms',
        f'{MEMBER_CALL_REPEATS} stub member calls')
    result['workload'] = 'stub-abort-v2'
    return result


def case_hook_call():
    """What answering that member from a host hook costs instead."""
    browser = _browser(interface_overrides={"AbortController": {"abort": lambda *_: None}})
    return _sample(_member_call_seconds(browser, expect_aborted=False), "ms",
                   f"{MEMBER_CALL_REPEATS} hooked member calls")


def case_abort_call():
    return _sample(_member_call_seconds(_browser(), expect_aborted=True), 'ms',
                   f'{MEMBER_CALL_REPEATS} implemented AbortController calls')


def case_dom_build():
    """Building nodes from script: createElement plus appendChild."""
    browser = _browser()
    page = _page(browser)
    try:
        elapsed = _js_seconds(page, (
            "const root = document.body;"
            f" for (let i = 0; i < {DOM_NODES}; i++) {{"
            " const el = document.createElement('div'); el.className = 'row';"
            " el.appendChild(document.createElement('span')); root.appendChild(el); }"
        ))
        return _sample(elapsed * 1000, "ms", f"{DOM_NODES} divs with a span each")
    finally:
        browser.close()


def case_dom_parse():
    """Handing markup to the parser: set_content with the same tree."""
    browser = _browser()
    page = _page(browser)
    markup = _document(PARSE_NODES)
    try:
        return _sample(_seconds_per(
            lambda: page.set_content(markup, url="http://benchmark.test/"), 1) * 1000,
            "ms", f"{PARSE_NODES} rows")
    finally:
        browser.close()


def case_dom_query():
    """Scanning a built tree: querySelectorAll and getElementsByTagName."""
    browser = _browser()
    page = _page(browser, _document(DOM_NODES))
    try:
        elapsed = _js_seconds(page, (
            f"for (let i = 0; i < {QUERY_REPEATS}; i++) {{"
            " document.querySelectorAll('div.row').length; document.getElementsByTagName('span').length; }"
        ))
        result = _sample(elapsed * 1000, "ms", f"{QUERY_REPEATS} scans of a {DOM_NODES}-row tree")
        result['workload'] = 'consumed-dom-query-v2'
        return result
    finally:
        browser.close()


def case_event_dispatch():
    """Dispatching a bubbling event to a listener."""
    browser = _browser()
    page = _page(browser, "<body><div id='host'><span id='leaf'></span></div></body>")
    try:
        elapsed = _js_seconds(page, (
            "const leaf = document.getElementById('leaf'); let seen = 0;"
            " document.getElementById('host').addEventListener('ping', () => { seen += 1; });"
            f" for (let i = 0; i < {DISPATCH_REPEATS}; i++)"
            " leaf.dispatchEvent(new Event('ping', {bubbles: true}));"
        ))
        return _sample(elapsed * 1000, "ms", f"{DISPATCH_REPEATS} dispatches through one listener")
    finally:
        browser.close()


def case_resource_batch():
    """Loading the subresources of one document through a host loader."""
    markup = "".join(f"<img src='/image{i}.gif'>" for i in range(RESOURCE_COUNT))
    browser = _browser(resource_loader=_loader)
    page = browser.new_profile().new_page()
    try:
        def operation():
            page.set_content(markup, url="http://benchmark.test/")
            page.run_until_idle(timeout=30)
        return _sample(_seconds_per(operation, 1) * 1000, "ms", f"{RESOURCE_COUNT} images")
    finally:
        browser.close()


def case_resident_pages():
    """Measure one memory batch in a fresh process, independent of prior cases."""
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--memory-worker'],
                           capture_output=True, text=True, check=True)
    return json.loads(child.stdout)


def _resident_pages_worker():
    from pyv8c.foundation.logging import logger
    logger.remove()
    browser = _browser()
    profile = browser.new_profile()
    pages = []
    try:
        # Separate retained pages from one-time V8/binder initialization.
        warm = profile.new_page()
        warm.set_content('<body>x</body>')
        warm.close()
        warm.executor._thread.join(5)
        gc.collect()
        before = _committed_bytes()
        pages = [profile.new_page() for _ in range(RESIDENT_PAGES)]
        for page in pages:
            page.set_content("<body><div>x</div></body>")
        after = _committed_bytes()
        contexts = [page.v8_context for page in pages]
        handles = sum(context.live_handles for context in contexts)
        result = _sample((after - before) / RESIDENT_PAGES / (1024 * 1024), 'MB/page',
                         f'{RESIDENT_PAGES} live pages, {handles // RESIDENT_PAGES} python handles each')
        result.update(workload='isolated-resident-pages-v2', private_before=before,
                      private_resident=after, handles_resident=handles)
    finally:
        browser.close()
    # Page closes from its owner thread. A repeated executor.close() already
    # sees "closed" and does not join that thread's final unwind.
    for thread in threading.enumerate():
        if thread.name.startswith('pyv8c-'): thread.join(5)
    gc.collect()
    result.update(private_after_close=_committed_bytes(),
                  handles_after_close=sum(context.live_handles for context in contexts),
                  owner_threads_after_close=sum(thread.name.startswith('pyv8c-')
                                                for thread in threading.enumerate()))
    return result


CASES = (
    ("startup", case_startup),
    ("cold_compile", case_cold_compile),
    ("page_cycle", case_page_cycle),
    ("evaluate", case_evaluate),
    ("host_call", case_host_call),
    ("js_loop", case_js_loop),
    ("stub_call", case_stub_call),
    ("hook_call", case_hook_call),
    ("abort_call", case_abort_call),
    ("dom_build", case_dom_build),
    ("dom_parse", case_dom_parse),
    ("dom_query", case_dom_query),
    ("event_dispatch", case_event_dispatch),
    ("resource_batch", case_resource_batch),
    ("resident_pages", case_resident_pages),
)


def measure(case, runs):
    """``(result, reason)``: the median and minimum of *runs* runs, or why there is none."""
    samples = []
    for _ in range(runs):
        try:
            samples.append(case())
        except Exception as exc:  # one case failing must not stop the others
            return None, f"{type(exc).__name__}: {exc}"
    first = dict(samples[0])
    first["median"] = statistics.median(sample["median"] for sample in samples)
    first["min"] = min(sample["min"] for sample in samples)
    first["runs"] = runs
    first["samples"] = samples
    return first, None


def record(results):
    baseline = {
        "recorded": time.strftime("%Y-%m-%d"),
        "python": sys.version.split()[0],
        "cases": {name: result for name, (result, _) in results.items() if result is not None},
        "unavailable": {name: reason for name, (result, reason) in results.items()
                        if result is None},
    }
    BASELINE_FILE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(baseline['cases'])} cases in {BASELINE_FILE.name}")
    for name in results:
        result = results[name][0]
        if result is not None:
            print(f"  {name:18s}{result['median']:10.2f} {result['unit']:10s}{result['detail']}")


def compare(results):
    if not BASELINE_FILE.exists():
        print("-- no baseline recorded; run with --record")
        return all(result is not None for result, _ in results.values())
    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    print(f"{'case':18s}{'baseline':>10s}{'now':>10s}{'delta':>9s}  unit       detail")
    regressed = []
    incomparable = []
    for name, (result, reason) in results.items():
        if result is None:
            print(f"{name:18s}{'--':>10s}{'--':>10s}{'':9s}  {reason}")
            continue
        recorded = baseline["cases"].get(name)
        now, unit, detail = result["median"], result["unit"], result["detail"]
        if recorded is None:
            print(f"{name:18s}{'new':>10s}{now:10.2f}{'':9s}  {unit:10s}{detail}")
            continue
        if result.get('workload') != recorded.get('workload'):
            print(f'{name:18s}  incomparable workload; baseline was not changed')
            incomparable.append(name)
            continue
        before = recorded["median"]
        change = (now - before) / before if before else 0.0
        slower = change > TOLERANCE and result["min"] > recorded["min"] * (1 + TOLERANCE)
        if slower:
            regressed.append(name)
        flag = "  <-- slower" if slower else ""
        print(f"{name:18s}{before:10.2f}{now:10.2f}{change:+8.1%}{flag}  {unit:10s}{detail}")
    if regressed:
        print(f"\nslower: {', '.join(regressed)} (median and minimum both past {TOLERANCE:.0%})")
    return not regressed and not incomparable and all(result is not None for result, _ in results.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="write the baseline file")
    parser.add_argument("--output", type=Path, help="save measured samples and environment without changing the baseline")
    parser.add_argument("--runs", type=int, default=3, help="runs per case")
    parser.add_argument("--cases", default="", help="comma-separated subset of case names")
    parser.add_argument('--memory-worker', action='store_true', help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.memory_worker:
        print(json.dumps(_resident_pages_worker()))
        return 0
    if arguments.runs < 1:
        parser.error("--runs must be positive")

    available = dict(CASES)
    selected = [name.strip() for name in arguments.cases.split(",") if name.strip()]
    unknown = [name for name in selected if name not in available]
    if unknown:
        parser.error(f"unknown case(s): {', '.join(unknown)}")
    chosen = [(name, case) for name, case in CASES if not selected or name in selected]

    results = {}
    for name, case in chosen:
        results[name] = measure(case, arguments.runs)
        if results[name][1] is not None:
            print(f"-- {name}: {results[name][1]}")

    if arguments.output:
        import pyv8
        binary = Path(pyv8._pyv8.__file__)
        report = {"python": sys.version, "platform": platform.platform(),
                  "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                  "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                  "worktree_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=REPO)).hexdigest(),
                  "cases": {name: {"result": result, "error": reason} for name, (result, reason) in results.items()}}
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if arguments.record:
        record(results)
        return int(any(result is None for result, _ in results.values()))
    return 0 if compare(results) else 1


if __name__ == "__main__":
    sys.exit(main())
