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
- `ConstantCallbackSpec`: `name` and a Faker JavaScript `callback` name.

Python callbacks accept a callable or a class member name, resolved once when
binding. Instance/prototype callbacks receive the Python receiver unless already
bound; interface callbacks do not. Attribute/method `callback_type=CallbackType.kJs`
requires a string referencing `globalThis.faker`, never a Python callable.
Callbacks are retained by the template; later class-member reassignment does not
change an installed explicit binding. Callback function objects are not modified.

## Template fields

`length` is 0..2147483647; `array` uses bits 0..4; `immutable` uses bits 0..1;
`has_constructor` is 0 or 1; `constructor_behavior` uses bits 0..1.
`global_this, undetectable, as_object, unconstructable, hidden, skip_inherit`
are booleans. `undetectable=True` requires an explicit `call` callback.
`parent_class` is a type or None; `name` and `to_string_tag` are strings or None.
`call` and `constructor_adapter` are callback references.

`name_*, index_*, cross_origin_check, cross_origin_name_*, cross_origin_index_*`
contain callback references, not enablement flags. None disables a handler;
providing a name/callable enables it. Suffixes are get, set, query, enum, del.
Unknown fields, invalid enum/flag ranges and malformed member types fail at
construction. Runtime receiver, cross-origin and argument checks remain.

## Registration and compilation

`Context(class_configs={Interface: spec})` copies the registry. A complete
spec replaces the class declaration rather than overlaying it. Within one
Context, `update_configs` accepts the same spec object repeatedly as a no-op;
a different object for an already registered type raises. Batch validation and
registry replacement are atomic. Registration after template installation is rejected.
Configuration changes require a new Context.

pyv8c's interface decorator records only identity, parent and exposure. A single
Universe compiler combines baseline metadata, own declarations and implementation
mixins, resolves signatures/callbacks and caches the immutable result. JS parent
members stay on the prototype chain. Realm-independent wrappers are shared;
only callbacks requiring a realm are rebound. BindingSession stages all ancestors
before native registration and publishes its state only on success.
`strict_validation` performs this compilation audit once per Universe.

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
