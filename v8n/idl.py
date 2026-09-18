"""The declarations a host writes about its interfaces.

The previous binding implemented these as native objects: one C++ type per
declaration, every field read out across the language boundary as the compiler
walked it.  Nothing about that needed to be native.  A declaration is data --
names, flags and callables -- so it is written here as data, and the only thing
that still crosses into C++ is the flat description the batch reader takes.

The shapes are the ones pyv8c already writes, field for field and default for
default, so a declaration did not have to be re-learned when the binding under
it changed.  Two of them keep a few odd spellings on purpose:

* ``callback_type`` distinguishes a Python callback from a JavaScript patch that
  the host installs itself.  Only the Python form can be described to the batch
  reader, and a description that names a patch is refused rather than written
  without it.
* the ``cross_origin_*_check`` flags read inverted from what they mean -- zero is
  "run the check" -- because that is how V8's access checks are spelled, and a
  declaration that had to translate them would be a chance to get it wrong.

``replace`` is the one operation the compiler leans on: it re-derives a
declaration with one or two fields changed, so a member that needs a bound
realm callback is a copy of the compiled member rather than a second
construction of it.
"""

from __future__ import annotations

import dataclasses
from typing import Any

__all__ = [
    "AttributeSpec",
    "ConstantCallbackSpec",
    "ConstantSpec",
    "IdlSpec",
    "MethodSpec",
]

# Both spec classes below are plain records.  ``eq=False`` because a declaration
# is identified by the class attribute that holds it, not by its fields: two
# interfaces may legitimately declare the same members, and equality would make
# them indistinguishable in a cache keyed by the declaration.
_record = dataclasses.dataclass(frozen=True, eq=False)


@_record
class AttributeSpec:
    """One property of an interface."""

    name: str
    getter: Any
    setter: Any = None
    #: ``CallbackType.kPy`` or ``CallbackType.kJs``.
    callback_type: int = 0
    #: ``FlagLocation``; the prototype unless the declaration says otherwise.
    location: int = 1
    #: ``V8Attribute`` bits.
    property_flags: int = 0
    #: ``FlagReceiverCheck``.
    receiver_check: int = 0
    #: ``FlagCrossOriginCheck``; zero means the check runs.
    cross_origin_get_check: int = 0
    cross_origin_set_check: int = 0
    #: ``SideEffectType``.
    side_effect: int = 0
    #: A write replaces the accessor with a data property on that one object.
    replaceable: bool = False

    def replace(self, **changes: Any) -> "AttributeSpec":
        return dataclasses.replace(self, **changes)


@_record
class MethodSpec:
    """One operation of an interface."""

    name: str
    callback: Any
    #: The declared function length.
    length: int = 0
    callback_type: int = 0
    location: int = 1
    property_flags: int = 0
    receiver_check: int = 0
    cross_origin_check: int = 0
    side_effect: int = 0

    def replace(self, **changes: Any) -> "MethodSpec":
        return dataclasses.replace(self, **changes)


@_record
class ConstantSpec:
    """A number installed on both the prototype and the interface."""

    name: str
    value: int = 0

    def replace(self, **changes: Any) -> "ConstantSpec":
        return dataclasses.replace(self, **changes)


@_record
class ConstantCallbackSpec:
    """A number produced by a callback, installed as a lazy data property.

    The callback runs only if something reads the name, which is what makes a
    constant that costs a script execution to compute affordable on an interface
    a page may never touch.
    """

    name: str
    callback: Any

    def replace(self, **changes: Any) -> "ConstantCallbackSpec":
        return dataclasses.replace(self, **changes)


#: The interceptor roles, in the order the batch reader lists them.
_NAMED_HANDLER_FIELDS = ("name_get", "name_set", "name_query", "name_del", "name_enum")
_INDEXED_HANDLER_FIELDS = ("index_get", "index_set", "index_query", "index_del", "index_enum")
_CROSS_ORIGIN_FIELDS = tuple(
    f"cross_origin_{field}"
    for field in _NAMED_HANDLER_FIELDS + _INDEXED_HANDLER_FIELDS
)


@_record
class IdlSpec:
    """Everything one interface declares about itself.

    The default is an interface with a name, no members and no constructor,
    which is what a namespace or a mixin looks like.  Every field a declaration
    may set is present here even where pyv8c never sets it, because a field the
    reader cannot see is a field a declaration cannot use.
    """

    #: The constructor's length.
    length: int = 0
    #: ``FlagArrayProto`` bits; zero for an interface that is not a collection.
    array: int = 0
    #: ``FlagImmutable`` bits.
    immutable: int = 0
    #: Whether the prototype keeps its synthetic ``constructor`` property.
    has_constructor: int = 0
    #: ``FlagConstructor`` bits.
    constructor_behavior: int = 0
    #: Install this interface as the realm's global object.
    global_this: bool = False
    #: ``document.all``-style: ``typeof`` reports undefined.
    undetectable: bool = False
    #: Expose an instance on the global instead of a constructor.
    as_object: bool = False
    #: Constructing throws even though the interface declares a constructor.
    unconstructable: bool = False
    #: Declared but not exposed; kept for completeness with the baseline.
    hidden: bool = False
    #: Do not build a prototype chain to the parent in this batch.
    skip_inherit: bool = False

    attributes: tuple = ()
    methods: tuple = ()
    constants_value: tuple = ()
    constants_callback: tuple = ()

    #: Makes instances callable, called as ``call(instance, *args)``.
    call: Any = None
    #: Called as ``adapter(cls, args, kwargs)``; bound to its class by the
    #: description builder, which is what lets the reader take one callable.
    constructor_adapter: Any = None
    #: The Python class this interface stands for, when it has one.
    parent_class: Any = None
    #: ``Symbol.toStringTag``.
    to_string_tag: Any = None
    #: The interface's own name, when it differs from the class name.
    name: Any = None

    # -- interceptors ------------------------------------------------------
    #
    # A role the host declares nothing for is absent rather than present and
    # declining, which is what lets a lookup fall through to the prototype chain
    # instead of routing through Python to be told "not mine".
    name_get: Any = None
    name_set: Any = None
    name_query: Any = None
    name_enum: Any = None
    name_del: Any = None
    index_get: Any = None
    index_set: Any = None
    index_query: Any = None
    index_enum: Any = None
    index_del: Any = None

    # -- cross-origin policy -----------------------------------------------
    #
    # A check, and optionally a second table of handlers used only when the
    # access is cross-origin.  Leaking the same-origin handlers to a stranger is
    # the one thing the policy exists to prevent, so the second table is
    # separate rather than a fallback.
    cross_origin_check: Any = None
    cross_origin_name_get: Any = None
    cross_origin_name_set: Any = None
    cross_origin_name_query: Any = None
    cross_origin_name_enum: Any = None
    cross_origin_name_del: Any = None
    cross_origin_index_get: Any = None
    cross_origin_index_set: Any = None
    cross_origin_index_query: Any = None
    cross_origin_index_enum: Any = None
    cross_origin_index_del: Any = None

    def replace(self, **changes: Any) -> "IdlSpec":
        return dataclasses.replace(self, **changes)
