# Source layout

`src/` holds 69 files (34 `.cpp` and 35 `.h`, about 11.9k lines) that used to sit
flat in one directory, next to a `faker/` subdirectory that had to reach back up
with `#include "../runtime.h"`. Flat made two questions hard to answer: where a
new file belongs, and what a file is allowed to use. This document answers both,
and `tools/check_layout.py` checks the second answer against the tree -- along
with the two build files, which have to agree with it. Both are gone now: every
group is one flat directory, and the `faker` object that subdirectory held is no
longer part of the binding.

This is the *where*. For the *why* -- how the isolate, the realm, the pair table
and the handle types hold each other up, and what each layer is for -- see
`doc/ARCHITECTURE.md`.

## Groups

| group | layer | owns | files |
| --- | --- | --- | --- |
| `src/` | 0 | portability shims and the module entry point; included by everything | `pyv8.h` `pyv8.cpp` `polyfill.h` `abi3.h/.cpp` `v8_compat.h` `boundary.h` `callback_boundary.h` |
| `src/isolate/` | 1 | the V8 platform, the isolate, the realm and their lifetimes | `platform_state.*` `v8_string_cache.*` `isolate.*` `isolate_state.*` `isolate_scope.*` `execution.*` `runtime.*` `context.*` `context_names.cpp` `context_resources.cpp` `timeout_guard.*` `error_hooks.*` |
| `src/values/` | 2 | values crossing the boundary, error mapping, handle registration | `convert.h` `convert_to_python.cpp` `convert_to_javascript.cpp` `exception.*` `jsvalue.*` `jsnull.*` `jsundefined.*` `handle_resource.h` |
| `src/handles/` | 3 | the Python types that stand for JavaScript values | `jsobject.*` `jsfunction.*` `jspromise.*` `jstypedarray.*` `script.*` |
| `src/tools/` | 3 | tracing -- the hook the interface layer wraps its entry points in -- and the devtools bridge | `trace.h` `debugger.*` |
| `src/interfaces/` | 4 | declarations into templates into callbacks | `idl_spec.*` `pyclass.*` `pyclass_handlers.*` `pyclass_interceptors.cpp` `pyclass_cross_origin_handlers.*` `v8tools.*` `pyclass_callbacks.cpp` `pyfunction.*` |

8 + 22 + 12 + 10 + 3 + 14 = 69.

`tools/` sits at the same layer as `handles/`, not above `interfaces/`. The
tracing hook is included *by* the interface layer (`pyclass_handlers.cpp`,
`v8tools.cpp`, `pyclass_callbacks.cpp`, `pyclass_interceptors.cpp`,
`pyfunction.cpp`) and by the realm's owner and its name installer, so it is
infrastructure those files use rather than something that uses them. The devtools
bridge is reached only from the composition points below.

## Allowed includes

A file may include headers from its own layer and from lower ones. Reaching
upward is what stops a layer from being one. Eight files exist to tie layers
together, and they are the whole exception list:

| file | may include | why |
| --- | --- | --- |
| `pyv8.h` | 0-2 | the aggregate header: every unit includes it, and it re-exports the shims and the two singletons |
| `pyv8.cpp` | everything | the module entry point registers every type |
| `context.cpp` | 1-3 | the realm owner builds the interface templates, creates the realm's handles and wires the devtools bridge |
| `context_names.cpp` | 1-4 | installing names into a realm and keeping the registry behind them is what `install`, `expose` and the lazy factory protocol do, so this is the one part of a context's life that knows the interface layer |
| `context_resources.cpp` | 1-3 | the pair table materialises a Python handle for a JavaScript object |
| `convert_to_python.cpp` | 1-3 | one direction of the converter: it builds handles for objects coming in, which the handles layer defines |
| `convert_to_javascript.cpp` | 1-4 | the other direction, which additionally has to know the class and function machinery |
| `isolate_state.cpp` | 1-3 | disposal is where what was built on an isolate is taken apart, and an inspector belongs to the isolate it was created against, so it has to go before `Isolate::Dispose` |
| `error_hooks.cpp` | 1-2 | reporting an isolate's errors means handing JavaScript values to Python, which is conversion |

Everything else, headers included, follows the plain rule. Two facts the checker
reports rather than hides:

* `pyv8.h` is a hub: 30 files include it, so touching it rebuilds most of the
  binding. That is why the shims stay at the root and stay small.
* A stale include is a violation nobody meant to write. The first pass found
  `isolate_state.cpp -> pyclass.h`, `exception.cpp -> script.h` and
  `context.cpp -> jsfunction.h` / `pyfunction.h`; the pass that flattened
  `tools/` found one more, `context.cpp -> trace.h`: the symbols those headers
  declare are not used by the files that include them. Dropping them belongs to
  the move that touches those directories, and the compiler is the judge.

## Where a new file goes

1. Which layer does it describe: the isolate and its lifetime, a value, a handle,
   an interface, or the tracing and dispatch that sits under the interfaces?
2. Which files will include it? Nothing below the layer it lands in may.
3. If the answer is "it has to reach upward", it is a composition point: write it
   into this document, add it to `tools/check_layout.py`, and keep the list short.

## Moving the tree

Staged so that every step is a plain move plus a build-configuration change, each
verified on its own.

| stage | moves | also |
| --- | --- | --- |
| S1 | `isolate/` (21 files) and `tools/` (7 at the time, `faker/` included) | the two `setup.py` places, `faker/Faker.cpp`'s three `../` includes, `doc/DEVTOOLS.md`, the MSVC project paths |
| S2 | `values/` (11) and `handles/` (10) | - |
| S3 | `interfaces/` (13) | rename `binding_callbacks.cpp` to `pyclass_callbacks.cpp`; rename `pyclass_handlers_internal.h` to `pyclass_handlers.h` and move the interceptor entry declarations out of `pyclass.h`; move the internal-field layout from `pyclass.h` to `values/handle_resource.h`, where the handles that use it can reach it without reaching up; drop the stale includes above; make the checker enforcing |
| S5 | nothing moves | splitting the three files that were past a thousand lines: `convert.cpp` into one file per direction, `pyclass_handlers.cpp` into the machinery and the entry points V8 calls, `context.cpp` into the realm's lifecycle and the names it installs.  Each split lands on its own, with the same verification as a move, because a split rewrites the tree's shape without meaning to change its behaviour |

Two facts made the moves cheap. `setup.py` collects its sources by recursing over
`src` -- a new group must not need a new glob there -- and `include_dirs` names
each group, so a file that changes group keeps the bare includes it already had.

The last subdirectory went further than flattened: `tools/faker/` was taken apart
and the feature it implemented was removed. What the binding no longer has, in the
order it was reachable, is:

* the `faker` object itself -- the JavaScript-visible helpers `print`, `run_js`,
  `run_js_file`, `structuredClone`, `get_arg`, `set_arg`, `new_document_all`,
  `native_wrapper` and the `v8_version` string -- which `pyclass.cpp` used to
  install on the global of every class declaring `IdlSpec.global_this`;
* the dispatch that made a member implemented in JavaScript possible at all:
  `CallbackType` and the `callback_type` field on `AttributeSpec`/`MethodSpec`,
  the three `FakerJsBindingCallback` entry points that resolved a callback name
  against `globalThis.faker`, and the lazy-constant path (`IdlSpec.
  constants_callback` and `ConstantCallbackSpec`) that resolved a name the same
  way;
* `js_function_repr`'s `faker.raw_tostring` hook, which nothing could define any
  more once the object was gone -- a function's repr is its own conversion again;
* the string-cache ids only those code paths read: `Faker`, `RawToString`, `Name`,
  `Length` and `Callback`.

`IdlSpec.global_this` stays: it says which Python class backs the JavaScript
global object. The tracing hook stays as well and is not part of any of this -- it
was only *in* the `faker` namespace, so with the object gone it answers to
`trace::`, which is what its call sites say now. What remains in `tools/` is
`trace.h` and the devtools bridge.

After each move: delete `build/temp*` (MSVC names object files after the source
path, so a move would otherwise link stale objects), rebuild, then run

```
python -m pytest tests -q                          # 338 passed
python -m pytest tests/unit tests/integration -q   # 372 passed, 1 environment failure
python -m pytest tests/browser -q                  # 210 passed
python benchmarks/bench_object_pairs.py
python benchmarks/bench_strings.py
```

The one unit failure is `test_idl_extractor.py`'s renderer snapshot test,
which needs a `_pyv8env/renderer` checkout that is not part of this tree.

Nothing in this plan changes behaviour, so the criterion is that those numbers do
not move beyond their own run-to-run noise: a move that changes a test result is
a bug in the move.
