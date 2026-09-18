# v8n — the native kernel behind pyv8c

`native/` is a from-scratch replacement for the binding in `../src`.  That tree
stays untouched and is now read-only reference material: it is not part of this
build, and nothing here includes its headers.

The wheel is a single abi3 artifact covering CPython 3.12, 3.13 and 3.14.

## Why a second implementation

The previous binding grew around a process-wide isolate that any thread could
enter.  Keeping that safe required a `v8::Locker` plus a GIL hand-off at every
entry point, a hand maintained reference count for every retained V8 handle, and
four parallel implementations of the same "isolate + persistent handle + Python
owner" bookkeeping.  Each of those is a place where a mistake is silent.

This kernel removes the possibility instead of coordinating it:

| Concern | Old | New |
| --- | --- | --- |
| Thread entry | any thread, `v8::Locker` + GIL juggling per entry | one owner thread per isolate, asserted at the boundary |
| Handle lifetime | `retain_v8_handle()` / `release_v8_handle()` by hand | `HandleSlot<T>` owns the `Global<T>`; construction and destruction are the count |
| Resource ownership | 32 fields on the context object | one `Realm` plus a registry that `tp_traverse` walks |
| Interface install | one cross-language round trip per member | one flat descriptor per realm, one install pass |
| Error handling | three conventions (`PY_PROPAGATE` macro, `goto`, hand written decrefs) | `PyRef` plus `raise()`, one path |
| Value conversion | eager enumeration of JS objects | three-state boundary; `to_python()` materialises on request |
| abi3 shims | spread over `abi3.h` and every call site | confined to `src/abi3/` |

## Layout

```
native/
├── include/v8n/          public contracts: kernel, realm, bridge, value, boundary
├── src/
│   ├── abi3/             everything Py_LIMITED_API makes awkward
│   ├── boundary/         PyRef, error raising, the two entry guards
│   ├── kernel/           platform, isolate, owner-thread rule, handle slots
│   ├── realm/            realm state machine, resource registry, hooks
│   ├── bridge/           IDL descriptors, template building, dispatch
│   ├── value/            handles, to_python / to_js, primitives
│   ├── inspect/          Inspector sessions and the debugger handle
│   └── module/           module entry, type registry
├── tests/                the kernel's own tests
└── benchmarks/           the same workloads the old binding was measured on
```

The Python facade lives one level up, at `../v8n`, and is reached through
`package-dir` in `pyproject.toml` so that both a checkout and a wheel expose a
single importable `v8n` package. It carries three things the extension does
not: `flag.py`, the vocabulary an interface declaration is written in;
`idl.py`, the declarations themselves as plain records; and `trace.py`, the
formatting for the one trace hook a realm can carry. None of them needs to be
native, because a declaration is data and the only part that crosses is the
flat description the batch reader takes.

## Building

V8 comes from the parent project.  `v8_build.py` already checks out, patches and
builds V8 15.2.20.1 into `_pyv8/.pyv8-build`, and this setup reuses that
configuration so both extensions link the same library, headers and ICU data.

```sh
cd _pyv8/native
python setup.py build_ext --inplace      # uses the cached V8
python setup.py build_v8                 # download and build V8 if missing
```

Two macros are compiled in from the selected V8 build and must not be dropped:

* `PYV8_ICU_DATA_SHA256` — the extension refuses to start against another
  build's `icudtl.dat` instead of faulting inside ICU.
* `V8_CPPGC_MICROTASK_QUEUE` — `v8::MicrotaskQueue`'s layout follows this macro.
  Built without it, the extension disagrees with the library about the class, so
  the answer is recorded at build time and the microtask API refuses to run
  rather than call through the wrong vtable slot.

`build/v8n-extension.json` records the V8 the extension was last built against;
a changed V8 forces a rebuild even though no source timestamp moved.

## Verifying

```sh
python -c "import v8n; print(v8n.v8_version())"
python -m pytest tests            # from native/
```

## A limit worth knowing

A realm's global object can carry about a thousand properties before V8 stops
treating it as a fast-properties object, and the next global variable load then
aborts inside V8 (`Unexpected instance type encountered`, or
`Isolate::PushStackTraceAndDie`) rather than failing in a way a host can catch.

Both forms of exposure count, accessors and lazy data properties alike, and the
ceiling does not depend on which names are used -- a realm with 900 properties
reads its global fine, one with 1000 does not. A host that declares more exposed
names than that has to serve the remainder through the global object's own
interceptor, which costs one Python call per read instead of one template
property per name and is not subject to the limit.
