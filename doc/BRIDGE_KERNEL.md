# Bridge kernel

The bridge requires the CPython 3.12 Stable ABI. A wheel built on 3.12 is
intended for regular GIL-enabled CPython 3.12, 3.13 and 3.14 on the same OS
and architecture. Free-threaded Python and subinterpreters are not supported.
V8 remains 15.2.20.1.

## Values

`Context.exec_js`, JS function calls, property access and host callback
arguments return primitives, the original Python host object, or a JS handle.
They do not enumerate JS objects or invoke accessors to convert them.
The `preserve_arguments` switch and `_to_python` method have been removed.

```python
result = context.exec_js('({items: [1, 2]})')
assert isinstance(result, pyv8.JSObject)
assert result.to_python() == {'items': [1, 2]}
```

`to_python()` recursively materializes arrays and plain objects, preserving
cycles and shared references. It can run getters and propagate their errors.
Other objects, including functions and typed arrays, remain handles.
JS identity is preserved when handles return to V8; Python wrapper identity
is not promised. Function property access preserves its receiver.

In pyv8c, `Page.evaluate()` materializes the result on the page executor;
`Page.evaluate_handle()` returns the unmaterialized value. Internal Web API
code uses handles until its Web IDL conversion explicitly needs Python data.

## Ownership and entry

`ExecutionScope` pins the Context and subject, acquires isolate access and
validates the Context again after waiting. Same-thread nested entries reuse
the existing locker. Waiting releases the GIL, and so does the window in which
V8 runs: a crossing into JavaScript gives it up and every callback boundary
takes it back before host code runs. Cleanup holds it. The current scheduling
topology is unchanged.

Contexts move from Active to Closing to Closed. Closing rejects new entry.
In-flight callbacks retain their metadata and finish before deferred teardown.
Closing invalidates all registered handles, including function receivers,
scripts and exception state. Explicit close and GC use the same resource
cleanup; GC does not dispatch page lifecycle events.

ContextResources indexes host owners, callback anchors, wrappers and realm
dependencies. Python references are visible through tp_traverse. The Isolate
coordinates V8 entry and pending cleanup, not strong Python callback roots.
V8 External values point to native ownership records rather than Python
objects. Weak callbacks enqueue preallocated records; decrefs occur outside
the V8 weak phase. Cross-isolate handles are rejected. Same-isolate transfers
conservatively retain the source until either Context closes.

PythonBoundary translates native C++ failures at Python API slots;
CallbackBoundary translates failures at installed host callback boundaries.
Allocation failure is not a promise that a V8 heap OOM can be recovered.
This is an in-process embedding, not a hostile-code security sandbox.

## Performance and validation

`benchmarks/bridge.py` runs five child processes, each with five warmup batches
and twenty measured batches. JSON includes raw samples, binary SHA-256 and
source revision. Run both checkouts with the same V8/compiler configuration.
The hot-path geometric mean includes callbacks, methods, accessors, host
object roundtrip and exceptions. Trace, DOM and page lifecycle are reported
separately. Run benchmarks without concurrent builds or tests.

```powershell
$env:PYTHONPATH='D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python benchmarks/bridge.py --output benchmarks/after.json --compare benchmarks/before.json
python benchmarks/lifecycle.py --count 1000 --output benchmarks/lifecycle-after.json
python -m pytest -q
python -m tox -e py312,py313,py314
```

The target is at least 15% lower hot-path geometric mean elapsed time, with
no stable regression above 5% for an individual core or lifecycle workload.
Cross-platform and sanitizer results must be reported separately from local
Windows tests; a configured validation command is not a successful run.
`tools/validate_linux.sh` enables ASan/UBSan for the bridge. Full V8 coverage
also requires a V8 library built with matching sanitizer instrumentation.
