# Schema 5 Interface Baseline

`pyv8c/baselines/interface-baseline.zip` is the published runtime snapshot of Chromium's
generated Blink V8 bindings. The extractor reads member installation and
template configuration from `v8_*.cc`, then supplements exposure, inheritance,
constructors, and constants from Web IDL.

The runtime expands the archive to `interface-baseline.json` in the same
directory on first load. That JSON file is a disposable local cache and is not
included in source control or built distributions. The extractor writes JSON by
default; use a `.zip` output name or `--zip` when producing the published
artifact.

Each object key is the Web IDL interface name:

```json
{
  "URL": {
    "parent": null,
    "tag": "URL",
    "exposed_in": ["Window", "Worker"],
    "constructor": {},
    "template": {},
    "members": {},
    "constants": []
  }
}
```

## Interface Identity

| Field | Meaning |
| --- | --- |
| `parent` | Parent Web IDL interface, or `null`. |
| `tag` | Tag used by `Object.prototype.toString.call(value)`. |
| `exposed_in` | Eligible execution contexts, such as `Window`, `Worker`, or `ServiceWorker`. |

The key is the interface name. There is no duplicate `identity` object.

## Constructor

| Field | Meaning |
| --- | --- |
| `exposed` | Whether Blink permits an interface object in the declared context. `LegacyNoInterfaceObject` makes this false. |
| `allow_new` | Whether `new Interface(...)` is valid. |
| `allow_call` | Whether `Interface(...)` is valid without `new`. |
| `callback` | Generated Chromium constructor callback name. |
| `length` | JavaScript constructor `Function.length`. |
| `signature` | Web IDL argument conversion and validation plan. |

`exposed_in` describes eligible environments; `exposed` describes whether the
interface object is permitted there; `allow_new` and `allow_call` describe
invocation behavior. The runtime exposure plan still decides whether a name is
installed in a particular V8 context.

## Template And Handlers

`template` maps Chromium V8 template behavior to `_pyv8` class-config overlay.

| Field | Chromium evidence |
| --- | --- |
| `immutable_proto` | `SetImmutableProto` |
| `undetectable` | `MarkAsUndetectable` |
| `global_this` | Global-this access-check template setup |
| `as_object` | Object-prototype template setup |
| `unconstructable` | Non-constructible template behavior |
| `array` | Array intrinsic/prototype setup |
| `call_handler` | `SetCallAsFunctionHandler` callback |
| `access_check` | `SetAccessCheckCallback` or `SetAccessCheckCallbackAndHandler` presence and mode |
| `handlers` | Named, indexed, and cross-origin property interceptor callbacks |

`template.handlers` has this structure:

```json
{
  "named": {"get": 1, "set": 1},
  "indexed": {},
  "cross_origin": {"named": {}, "indexed": {}}
}
```

Each group can contain `get`, `set`, `query`, `del`, `enum`, and `definer`.
There is no duplicate top-level `handlers` field.

## Members

Members are grouped by installation target:

| Group | Target |
| --- | --- |
| `instance` | Each wrapped instance. |
| `prototype` | The interface prototype. |
| `interface` | The interface object / constructor. |

An operation records `name`, `length`, `flags`, `receiver`, `cross_origin`,
`side_effect`, and `signature`. An attribute records `name`, `setter`, `flags`,
`receiver`, `cross_origin`,
`side_effect`, and optional `setter_signature`. A missing `setter` means readonly.

The default runtime baseline omits C++ callback symbols and default `world`
values. A mutable attribute keeps `setter: 1`; a readonly attribute keeps
`setter: null`. Non-default world values are retained.

The enclosing group determines installation location; members do not repeat a
`location` field. Constants are held only in `constants`, rather than being
duplicated in `members.interface`.

## Constants

| Field | Meaning |
| --- | --- |
| `name` | Constant name. |
| `expression` | Original Chromium/IDL expression; audit output only. |
| `resolved` | Whether static literal evaluation succeeded. |
| `value` | Evaluated JSON-safe value when resolved. |
| `location` | Installation target, normally `interface`. |

The default runtime baseline omits `expression`. Audit output includes it for
review. If `resolved` is false, runtime consumers must not invent a value.

## Signatures And Callbacks

`signature.status` is `generated`, `manual-reviewed`, or `unresolved`.
`source_evidence` records the generated-code evidence or why parsing failed.
Parameters are ordered; they record `idl_type`/`type`, `optional`, `nullable`,
and, where applicable, `default`, `variadic`, and `enum`.

Callback names and handler callback symbols are extraction audit data. They are
omitted from the default runtime baseline and retained when generated with
`tools/extract_interfaces.py --audit`. The runtime only needs the presence of
each handler role, represented as `1` in audit-stripped handler maps.
`access_check` retains `enabled: 1` and `with_handler` in the default output.
`_webidl.compact` is required to be `true` for a runtime baseline. An audit
baseline generated with `--audit` has `compact: false` and is rejected by the
runtime rather than being interpreted through compatibility logic.

## Optional Audit Data

`provenance.snapshot` identifies the Chromium generated-binding snapshot and
`provenance.sources` lists contributing generated C++ files. No C++ source body
is embedded in the baseline. It is omitted by default; pass `--audit` to
`tools/extract_interfaces.py` when generating an auditable baseline. The same
flag also retains callback symbols and `world` values.
