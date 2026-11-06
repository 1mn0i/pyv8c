
class FlagImmutable:
    instance = 1 << 0
    prototype = 1 << 1


class V8Attribute:
    kNone = 0
    ReadOnly = 1 << 0  # ReadOnly, i.e., not writable
    DontEnum = 1 << 1  # DontEnum, i.e., not enumerable.
    DontDelete = 1 << 2  # DontDelete, i.e., not configurable


class FlagLocation:
    kInstance = 0
    kPrototype = 1
    kInterface = 2


class FlagReceiverCheck:
    kCheck = 0
    kDoNotCheck = 1


class FlagCrossOriginCheck:
    kCheck = 0
    kDoNotCheck = 1


class FlagExposed:
    kNo = 0
    kYes = 1


# 控制js object对象是否能使用 "new Test()" 和 "Test()"语法
class FlagConstructor:
    kDontAll = 0
    kNew = 1 << 0
    kCall = 1 << 1
    kAll = kNew | kCall


class SideEffectType:
    kHasSideEffect = 0
    kHasNoSideEffect = 1
    kHasSideEffectToReceiver = 2


class ArrayProto:
    """Array intrinsics for indexed iteration, accepted by IdlSpec.array_methods.

    iterator installs only Symbol.iterator; iterable also installs entries, keys,
    values and forEach. Pair and set iteration use iteration_methods instead.
    """

    iterator = 1
    iterable = 31


class IterationKind:
    """Data semantics for IdlSpec.iteration_kind."""
    none = 0
    indexed = 1
    pair = 2
    set = 3


class DefinitionKind:
    """Which interface object an IDL definition builds, for ``IdlSpec.definition_kind``.

    The names are Blink's, from ``WrapperTypeInfo``
    (``third_party/blink/renderer/platform/bindings/wrapper_type_info.h``): ``kInterface`` is a
    function with a prototype, ``kIdlNamespace`` is a plain object tagged on itself
    (``SetupIDLNamespaceTemplate``), and ``kWrapperTypeNoPrototype`` is a function whose prototype
    Blink never makes -- the shape an IDL ``callback interface`` answers
    (``SetupIDLCallbackInterfaceTemplate``).

    The three are exclusive by construction -- a namespace has no prototype either, so the shape
    alone cannot tell it from a callback interface -- and an ordinary interface, ``kInterface``,
    is what the field defaults to.
    """

    kInterface = 0
    kIdlNamespace = 1
    kWrapperTypeNoPrototype = 2


__all__ = [
    "FlagImmutable", "V8Attribute", "FlagLocation",
    "FlagReceiverCheck", "FlagCrossOriginCheck", "FlagExposed",
    "FlagConstructor", "SideEffectType", "ArrayProto", "IterationKind", "DefinitionKind",
]
