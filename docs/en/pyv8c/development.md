# Development Guide

[简体中文](../../pyv8c/development.md) | **English**

[Documentation Index](../README.md)

## Environment and Working Conventions

Install the project with CPython 3.12 at the repository root, and confirm that the code loaded is from this repository:

```powershell
$env:PYTHONUTF8 = "1"
python312 -m pip install -e ".[dev,reference]"
python312 -c "import pyv8c; from pyv8c import pyv8; print(pyv8.__file__, pyv8c.__file__)"
```

Before building, you must configure the V8 build directory; see the native project documentation. When using Python 3.15, first build the ABI3 wheel with Python 3.12 as described below.

Before making changes, record the repository's working-tree state and preserve existing modifications. Verify the actually loaded native binary, interpreter, IDL, environment profile, and Chrome; after modifying C++, you must rebuild the extension in use and must not run Python tests only against the old binary.

For core rules, see [Architecture](architecture.md): fixes go into the layer that actually owns the behavior and state, and Web APIs do not depend directly on Page controllers; caches state the owning instance, invalidation, and release conditions explicitly, and do not establish process-global mutable page state.

## Building from Source

### Windows Local Build

When building a distribution package locally, first prepare V8 according to the [pyv8 documentation](../../../pyv8/README.md), then use CPython 3.12 at the repository root:

```powershell
py -3.12 -m pip install build wheel
py -3.12 -m build --wheel --outdir dist .
```

The generated `pyv8c-*-cp312-abi3-win_amd64.whl` is a single-package distribution.

### Linux Local Build

Ubuntu 22.04 x86-64 can prepare V8 and build the wheel natively on Linux, using a GIL-enabled
CPython 3.12, a C++20 compiler, and Python build tools:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel build
python setup.py build_v8
python -m build --wheel --no-isolation
python -m pip install dist/pyv8c-*-linux_x86_64.whl
```

If a proxy is needed, pass `--proxy=http://主机:端口` to `build_v8`. The first build downloads
the V8 and Chromium toolchains, which takes considerable time and disk space; control parallelism with `PYV8_V8_JOBS`.
The above Linux wheel has been verified for basic operation on Ubuntu 22.04 x86-64 and does not imply compatibility with all Linux distributions.

### Windows, Linux, and macOS CI

The repository's GitHub Actions workflow `Build wheels` can be triggered manually to select and build wheels for the three platforms in parallel.
All three platforms use GitHub-hosted runners; Linux uses Ubuntu 22.04 x64 and does not require a local VM.
In **Actions → Build wheels → Run workflow**, select the required platforms. The corresponding artifacts are
`windows-wheels`, `linux-wheels`, and `macos-arm64-wheels`.

Each Linux job starts on a fresh runner and currently downloads and builds V8 again, requiring considerable
time and disk space. Verify the available resources and build result in GitHub Actions.
All platform jobs check a post-install smoke test and a native crash smoke; Linux and macOS also check the native parameter bridge, and the Windows wheel test matrix also runs the architecture layering gate. None of this means the full browser test suite passes.

## Implementing an Interface

Interface declarations use `pyv8c.idl.interface`. Methods commonly use `fn_name`, and properties commonly use `get_name` and `set_name`; named/indexed and cross-origin handlers use the corresponding `__v8_*__` conventions. Member surface and property flags are based on the IDL; Python handles argument conversion, state, and actual semantics, and does not hand-write shapes already fixed by the baseline.

The generic Feature runtime mechanism lives in `pyv8c/pyv8c/features/runtime.py`, built-in declarations in `pyv8c/pyv8c/features/catalog.py`, and core interface assembly in `pyv8c/pyv8c/features/core.py`. Feature implementation modules explicitly export FEATURE and declare dependencies in FeatureSpec. Adding optional features should not make the base package import load all implementations or the native runtime. Pure metadata, extractors, and basic resource records must still support import without a native environment. For the directory ownership of Web APIs, see [Architecture](architecture.md#web-api-ownership).

Before development, first establish a minimal failing scenario, then fix it and add a regression test. Ordinary JS errors, host failures, execution termination, and resource failures are handled separately; when broadly catching `BaseException`, you must confirm whether execution control signals need to be propagated, and must not keep the host alive by swallowing termination and continuing the page flow.

Async callbacks validate cancellation, document generation, and identity. Real network, Python blocking, and worker-thread result delivery must not bypass the owner rules. When adding a public interface, update type declarations and callers at the same time; new lifecycle records must have a unified terminal state and a release path.

## Test Channels

```powershell
# Run native module and framework tests from the repository root, excluding slow
python -m pytest -q

# Run only browser framework tests
python -m pytest -c pytest.ini pyv8c/tests -q

# Regular main suite without Chrome comparison (with the native crash smoke)
python -m pytest -c pytest.ini pyv8c/tests/unit pyv8c/tests/integration pyv8c/tests/browser pyv8c/tests/native -q

# Native crash smoke only (separate child processes)
python -m pytest -c pytest.ini pyv8c/tests/native -q

# Slow lifecycle, heap budget, and policy review
python -m pytest -c pytest.ini pyv8c/tests -o addopts=--strict-markers -m slow -q

# Repeat race-marked cases at least five rounds
python -m pytest -c pytest.ini pyv8c/tests -o addopts=--strict-markers -m race --count=5 -q

# Architecture, change companion, startup, offline import, and duplicate case checks
python tools/check_policy.py
```

The default pytest configuration excludes slow, so `-m slow` also overrides addopts to avoid selector conflicts. Race tests may also be marked slow, and repeated runs likewise require the override. Do not run Chrome comparison in parallel by default.

When modifying native support, run the build, tests, and layout check at the repository root:

```powershell
python312 setup.py build_ext --inplace
python -m pytest -c pytest.ini pyv8/tests -q
python pyv8/tools/check_layout.py
```

The build requires the V8 SDK, compiler, and related settings required by this repository. Do not overwrite the target binary while other processes still have it loaded, and do not clean up processes not created by this run.

## Test Layers and Placement Rules

The five directories under `pyv8c/tests/` are not grouped by feature module but by **what a test needs in order to run** and **where its assertion gets its authority**. `tox.ini`, `pytest.ini` and `tools/check_policy.py` reference them directly, so putting a new test in the wrong layer breaks a gate.

| Directory | Source of truth | Runtime needs | Typical content |
| --- | --- | --- | --- |
| `unit` | Plain Python assertions | Must import without a compiled extension | Static architecture checks, IDL/metadata, argument conversion, CookieJar, tooling scripts |
| `browser` | One Web API's return value inside pyv8c | Offline (`default_loader` returns an empty 200), no Playwright | DOM/BOM interfaces, events, storage |
| `integration` | Whether several subsystems still hold together | Local HTTP/WebSocket servers, Workers, timers, thread teardown | Navigation, resources, execution budgets |
| `native` | Whether a child process stays alive | Separate process + `-X faulthandler` | `test_native_smoke.py`; a C++ segfault kills the pytest process, so the exit code can only be asserted from outside |
| `repros` | Whether real Chrome and pyv8c agree | Local Chrome + Playwright (`reference` extra) | The behaviour corpus in `corpus.py`; each case runs on both sides |

Placement rule: no V8 -> `unit`; one page behaviour -> `browser`; cross-component or real I/O -> `integration`; worried about a C++ crash -> `native`; needs an answer checked against Chrome -> `repros`.

Three hard constraints:

- The py312–py315 wheel matrix in `tox.ini` runs only `pyv8/tests` + `tests/unit` + `tests/browser` + `tests/integration`, deliberately excluding `native` and `repros`: the matrix validates one abi3 wheel across four CPython versions, cannot depend on a locally installed Chrome, and need not repeat a child-process smoke test four times.
- The `offline` gate of `tools/check_policy.py` sets `PYV8C_OFFLINE=1`, which makes `tests/conftest.py` hide `pyv8c.pyv8`, and then runs only `tests/unit`. Therefore **any unit test whose top-level imports reach the native extension must call `pytest.importorskip("pyv8c.pyv8")` before all `pyv8c` imports**. The gate runs a static pre-check first: it derives the transitive set of native-bound modules from the package's own import graph (including the separate `pyv8c._audio_native` extension) and reports the offending file and line. `@pytest.mark.native` only means "this case needs the compiled extension" and cannot replace that guard, because collection has already failed by then. A clean CI checkout must also set `PYV8C_OFFLINE=1`; otherwise `pytest.importorskip` re-raises `cannot import name '_pyv8'` instead of skipping.
- `pytest.ini` registers the `native`, `race` and `slow` markers and defaults to `-m "not slow"`. **`@pytest.mark.native` is not the `native/` directory**: the former marks a case as requiring the extension and also appears in `unit`; the latter means the test must be verified in a separate child process.

`repros` is a two-engine behaviour corpus, not an ordinary regression suite: the local Chrome version, the IDL baseline (`154.0.8030.0`) and the environment profile in `chrome-environment.json` (`156.0.8078.80`) are three different releases, so any drift fails a whole batch. The default `pytest -q` collects it too; the "regular main suite without Chrome comparison" above is the better signal for everyday changes. Do not edit `expected` values to make drift green.

`native/` is wired into the `Build wheels` workflow: the Windows wheel test matrix and the Linux and macOS build jobs each run the native crash smoke after installing the wheel, and the Windows matrix also runs the `layers` architecture gate on Python 3.12. When the source tree has no compiled extension the smoke test loads the installed wheel automatically; locally it keeps using the source tree as before. The `layers` gate spawns child processes that really import Web modules and the `startup` gate constructs a Browser, so both need a compiled extension and stay with the wheel jobs and local pre-commit; the PR gate in `.github/workflows/source-checks.yml` runs only `check_license.py`, the three source-only policy gates and the offline `tests/unit` suite.

## Chrome Behavior Comparison

```powershell
$env:PYV8C_CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"
python -m pytest -c pytest.ini pyv8c/tests/repros/test_execution_model_vs_chrome.py -q
```

The comparison tool uses the local executable; cases may be skipped when Chrome or Playwright is missing, and a skip does not count as a pass. Record the actual version and startup arguments; do not silently substitute another Chromium or modify the automation state.

Both sides use the same HTML, origin, response headers, scripts, and operation sequence. Real visits are used to discover problems, while a local server or fixture is used for stable acceptance; a fixture serves only explicit resources and fails on unknown requests. First compare navigation, resources, scripts, events, and readiness conditions to locate the earliest causal divergence, then inspect the interface surface or environment branches.

Distinguish framework defects, missing features, configuration/version/device differences, server responses, and probe interference. WebGL, media, permission, and network differences especially require recording environmental factors. A page's environment checks should be distilled into repeatable browser semantics, not replaced by constant success, site patches, or script substitution instead of framework fixes.

## Extraction and Performance

For IDL and environment capture commands, see [IDL Format](idl-format.md). Before generating artifacts, verify the source version and first write to an independent output for validation; do not overwrite the Chromium/V8/profile baseline without authorization.

The Window order comes from the environment profile's `bindings.own_order`; when updating the order, re-capture the target Chrome and record the version and capture conditions. The IDL records only interfaces and members and no longer stores a separate Window order baseline. The runtime uniformly installs the actually exposed properties, and the order of V8 built-in properties that cannot be satisfied is reported by the difference diagnosis.

For performance, profile before optimizing; measure in serial alternation for at least five rounds on the same machine, same interpreter, same source/binary identity, and same workload, recording the median, minimum, variance, and post-shutdown reclamation. Changes without a stable gain or that introduce a behavior regression are not kept.

```powershell
python benchmarks/benchmark.py --runs 5 --output .\benchmark-result.json
python benchmarks/measure_startup.py
```

These commands run measurements. `benchmark.py --record` and the policy tool's `--record-startup` rewrite the performance baseline and must not be used to eliminate regressions. Environment changes and workload changes require separate explanation; non-equivalent numbers must not be compared directly.

## Delivery Checklist

State the specific problem, trigger conditions, the fix in the owning layer, the regression test, and the actual verification results. Distinguish passed, unverified, skipped, and known failures; preserve the original evidence of race failures and do not claim the root cause has disappeared based on a successful rerun. Do not loosen assertions, widen exclusions, or update baselines to mask failures.

Documentation maintains only currently available interfaces and known limitations. Commands, links, and examples must be verified after changes; topic files uniformly use lowercase hyphenated names, the index uses README.md, and the body text is uniformly in Chinese.
