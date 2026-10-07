# Immutable native IDL specifications

All explicit binding metadata lives in `__v8_idl_spec__`, containing a native
`pyv8.IdlSpec`. Scalars are C++ fields and object fields hold GC-visible Python
references. All five descriptor types are immutable and keyword-only.

## Declaration

```python
import pyv8

class Example:
    __v8_idl_spec__ = pyv8.IdlSpec(
        constructor_behavior=pyv8.FlagConstructor.kNew,
        has_constructor=1, to_string_tag="Example",
        attributes=(pyv8.AttributeSpec(name="value", getter="get_value"),),
        methods=(pyv8.MethodSpec(name="add", callback="add", length=1),),
        constants_value=(pyv8.ConstantSpec(name="READY", value=1),),
    )

    def get_value(self):
        return 42

    def add(self, value):
        return 42 + value

configured = Example.__v8_idl_spec__.replace(length=1)
assert configured.attributes is Example.__v8_idl_spec__.attributes
```

Read fields with attribute access. `replace(**changes)` returns a new validated
object and shares unchanged immutable members. There is no mapping interface,
item assignment, deletion, positional record, or dictionary round trip.
See `pyv8/_pyv8.pyi` for complete keyword signatures and read-only properties.

## Member types

- `AttributeSpec`: required `name` and `getter`; optional `setter`; separate
  `cross_origin_get_check` and `cross_origin_set_check`; `location`,
  `property_flags`, `receiver_check`, `side_effect`, `replaceable`.
- `MethodSpec`: required `name` and `callback`; `length` in 0..255, `location`,
  `property_flags`, `receiver_check`, `cross_origin_check`, `side_effect`.
- `ConstantSpec`: `name` and integer `value` in -2147483648..4294967295.

Python callbacks accept a callable or a class member name, resolved once when
binding. Instance/prototype callbacks receive the Python receiver unless already
bound; interface callbacks do not.
Callbacks are retained by the template; later class-member reassignment does not
change an installed explicit binding. Callback function objects are not modified.

## Template fields

`length` is 0..2147483647; `immutable` uses bits 0..1;
`has_constructor` is 0 or 1; `constructor_behavior` uses bits 0..1.

`array` is what an interface borrows from `Array.prototype`, as the bits the binding installs:
`pyv8.ArrayProto.iterator` (1) installs `Array.prototype.values` behind `@@iterator` and nothing
else, which is what `HTMLCollection`, the plugin collections and `CSSStyleDeclaration` answer;
`pyv8.ArrayProto.iterable` (31) installs that iterator together with `entries`, `keys`, `values`
and `forEach`, which is what a `NodeList`, a `Headers`, a `FormData` or a Maplike answers. Both
shapes were measured against Chrome (2026-10-07) and no interface was found that wanted a third;
0, which is also what an unset field holds, borrows nothing. The intrinsics behind the bits are
the binding's own business.
`global_this, undetectable, unconstructable, hidden, skip_inherit, code_like` are booleans.
`undetectable=True` requires an explicit `call` callback.
`definition_kind` is the other numeric field: `pyv8.DefinitionKind.kInterface` (0, the default: a
function with a prototype), `kIdlNamespace` (1: a plain object, what Blink builds from an
`ObjectTemplate`) or `kWrapperTypeNoPrototype` (2: a function whose `prototype` Blink never
makes). Those are the shapes Blink's `WrapperTypeInfo` decides between, and the three are
exclusive, so a definition cannot be a namespace and a callback interface at once.
`code_like=True` marks the instance template code-like: `eval` and the `Function`
constructor stringify such a value instead of handing it back unchanged, which is what
lets a class like Blink's `TrustedScript` carry executable source. Every other object
still comes back untouched, as the specification says.
`parent_class` is a type or None; `name` and `to_string_tag` are strings or None.
`call` and `constructor_adapter` are callback references.

`name_*, index_*, cross_origin_check, cross_origin_name_*, cross_origin_index_*`
contain callback references, not enablement flags. None disables a handler;
providing a name/callable enables it. Suffixes are get, set, query, enum, del.
Unknown fields, invalid enum/flag ranges and malformed member types fail at
construction. Runtime receiver, cross-origin and argument checks remain.

## Registration and compilation

A realm keeps one entry per interface name it has met: the template it built and
the spec that built it. Specs arrive from one place, the realm's `lazy_factory`,
which is asked about a name the realm has neither a spec nor a template for.

The answer is one of three things:

- the class, whose declaration is then the spec;
- `(class, {name: spec})`, and the specs it carries are kept for the names they
  name — the whole ancestor chain a build walks, when the realm holds overlays
  for all of it. A complete spec replaces the class declaration rather than
  overlaying it;
- `None` for a name the realm does not know, which leaves the class declaration
  in place.

A refusal is remembered, so a name costs one ask per realm, and a factory that
raises is not an answer: the name stays open and is asked again. A spec named for
a name whose template already exists is ignored, so a template is always built
from the spec that arrived with its build and configuration cannot arrive after
installation. `Context.install(names)` installs each name as a non-enumerable
lazy constructor on the global; nothing is built for a name no script reads.

A realm's class templates are keyed by the class's own name, so one IDL name holds one
template and a second class claiming that name is refused.

pyv8c's interface decorator records only identity, parent and exposure. A single
binder combines baseline metadata, own declarations and implementation
mixins, resolves signatures/callbacks and caches the immutable result. JS parent
members stay on the prototype chain. Realm-independent wrappers are shared;
only callbacks requiring a realm are rebound. BindingSession stages all ancestors
before native registration and publishes its state only on success.
`strict_validation` performs this compilation audit once per `Bindings`.

## Breaking migration

- Replace tuple entries with the four named member descriptors.
- Replace `spec[key]`, `dict(spec)` and mutation with field access and `replace`.
- Replace interceptor booleans with callback names/callables or None.
- Move ConstructorConfig settings into IdlSpec; install hooks independently.
- Remove callback-owner classes, direct/raw-name markers and member decorators
  from explicit IDL declarations. No compatibility layer is supplied.

Objects without IdlSpec still use ordinary Python reflection and generic bridge
protocols. Trace and exception protocols remain separate runtime capabilities.

Generic class reflection cannot install arbitrary Python object instances as
template constants without a realm. This unsupported path now raises RuntimeError
instead of dereferencing an empty V8 context. Expose the object as a Context value
or provide an explicit AttributeSpec getter. Reflected callables are retained
before class metadata hooks can mutate the class or trigger garbage collection.
