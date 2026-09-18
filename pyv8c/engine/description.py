"""Turn compiled interfaces into the flat description the native batch API reads.

The previous binding carried a tree of native spec objects across the boundary --
an ``IdlSpec`` holding ``AttributeSpec`` and ``MethodSpec`` objects, each field
read out one at a time, each member committed as it arrived.  The new API reads one
flat mapping per install: a list of interfaces, one tuple of callables they index
by slot, and the names to build later.

There are two stages, and the seam between them is the point:

* a **plan** describes one interface with the callables themselves attached.  It is
  what the compiler produces, and it is worth compiling once for every realm that
  installs it.
* a **description** is what the native reader takes, and it refers to callables by
  their index in one tuple.

A slot is an index into one batch's tuple, so a slot is only correct for the batch
that assigned it.  Plans therefore carry none, and :class:`InterfaceDescription`
assigns them -- which is why the compiler can keep compiling once and a realm can
keep installing many times.

Everything here is data.  No spec objects survive either stage.
"""

from __future__ import annotations

from v8n.flag import (
    CallbackType,
    FlagCrossOriginCheck,
    FlagImmutable,
    FlagReceiverCheck,
    FlagLocation,
)

# A member's location, as the description spells it.
_LOCATIONS = {
    FlagLocation.kInstance: "instance",
    FlagLocation.kPrototype: "prototype",
    FlagLocation.kInterface: "interface",
}

# Interceptor roles, as (IdlSpec field, description key).  Both families are read by
# the same loop, because the role already says which one it belongs to.
_NAMED_ROLES = (
    ("name_get", "name_get"),
    ("name_set", "name_set"),
    ("name_query", "name_query"),
    ("name_del", "name_del"),
    ("name_enum", "name_enum"),
)
_INDEXED_ROLES = (
    ("index_get", "index_get"),
    ("index_set", "index_set"),
    ("index_query", "index_query"),
    ("index_del", "index_del"),
    ("index_enum", "index_enum"),
)

# ``IdlSpec.length`` arrives with this default, meaning "the constructor declares no
# length" rather than "the length is two billion".  A real length always comes from
# the baseline, so the sentinel is the only way to tell them apart.
_UNSET_LENGTH = 0x7FFFFFFF


class UnsupportedMember(Exception):
    """A declaration this build cannot describe, named rather than skipped."""


def bind_adapter(adapter, cls, name: str = "interface"):
    """The constructor adapter, as the native side calls it: ``adapter(args, kwargs)``.

    The old adapter took the class as its first argument and relied on the caller to
    supply one.  This one is bound here, because the reader has no way to know which
    class an adapter belongs to -- it is handed an adapter and nothing else.
    """

    def construct(args, kwargs, _adapter=adapter, _cls=cls):
        return _adapter(_cls, args, kwargs)

    construct.__name__ = f"_pyv8c_adapt_{getattr(cls, '__name__', name)}"
    return construct


def plan_from_spec(spec, *, name: str, implementation=None, parent: str | None = None) -> dict:
    """Read a compiled declaration into a plan.

    ``spec`` is the compiled declaration, ``name`` is the interface's name -- which
    is also the constructor's name and the name it is exposed under -- and
    ``implementation`` is the Python class it stands for.  The class is what lets an
    instance built in Python arrive in JavaScript as an instance of this interface
    rather than as a generic host object, so passing it is worth the trouble for any
    interface whose objects a host constructs itself.
    """
    plan: dict = {"name": name, "members": []}

    if parent is not None:
        plan["parent"] = parent

    traits = _traits(spec)
    if traits:
        plan["traits"] = traits

    tag = getattr(spec, "to_string_tag", None)
    if tag:
        plan["to_string_tag"] = str(tag)

    length = int(getattr(spec, "length", _UNSET_LENGTH))
    if length != _UNSET_LENGTH:
        plan["length"] = length

    forms = _constructor_forms(spec)
    if forms:
        plan["constructor"] = forms

    if implementation is not None:
        plan["type"] = implementation

    if getattr(spec, "constructor_adapter", None) is not None:
        plan["constructor_adapter"] = spec.constructor_adapter

    if getattr(spec, "call", None) is not None:
        plan["call"] = spec.call

    array = int(getattr(spec, "array", 0))
    if array:
        plan["array"] = array

    handlers, cross_origin = _handlers(spec)
    if handlers:
        plan["handlers"] = handlers
    if cross_origin:
        plan["cross_origin"] = cross_origin

    for member in getattr(spec, "attributes", ()):
        plan["members"].append(_attribute_plan(member))
    for member in getattr(spec, "methods", ()):
        plan["members"].append(_method_plan(member))

    for constant in getattr(spec, "constants_value", ()):
        plan["members"].append({
            "kind": "constant",
            "name": constant.name,
            "value": int(constant.value),
        })
    for constant in getattr(spec, "constants_callback", ()):
        plan["members"].append({
            "kind": "constant_callback",
            "name": constant.name,
            "callback": constant.callback,
        })

    return plan


def _traits(spec) -> list[str]:
    traits = []
    if getattr(spec, "global_this", False):
        traits.append("global_this")
    if getattr(spec, "undetectable", False):
        traits.append("undetectable")
    if getattr(spec, "unconstructable", False):
        traits.append("unconstructable")
    if getattr(spec, "as_object", False):
        traits.append("as_object")
    if getattr(spec, "skip_inherit", False):
        traits.append("skip_inherit")
    if int(getattr(spec, "has_constructor", 0)):
        traits.append("has_constructor")

    immutable = int(getattr(spec, "immutable", 0))
    if immutable & FlagImmutable.prototype:
        traits.append("immutable_prototype")
    if immutable & FlagImmutable.instance:
        traits.append("immutable_instance")
    return traits


def _constructor_forms(spec) -> list[str]:
    behavior = int(getattr(spec, "constructor_behavior", 0))
    forms = []
    if behavior & 0b01:
        forms.append("new")
    if behavior & 0b10:
        forms.append("call")
    return forms


def _handlers(spec) -> tuple[dict, dict]:
    """The interceptors a declaration answers with, as role -> callable.

    A plan carries the callable itself; the batch assigns the slot.  Roles the host
    declared nothing for are absent rather than present-and-declining, which is what
    lets a lookup fall through to the prototype chain instead of routing through
    Python to be told "not mine".
    """
    handlers = {}
    for field, key in _NAMED_ROLES + _INDEXED_ROLES:
        value = getattr(spec, field, None)
        if value is not None:
            handlers[key] = value

    cross_origin = {}
    for field, key in _NAMED_ROLES + _INDEXED_ROLES:
        value = getattr(spec, f"cross_origin_{field}", None)
        if value is not None:
            cross_origin[key] = value
    if getattr(spec, "cross_origin_check", None) is not None:
        cross_origin["check"] = spec.cross_origin_check
    return handlers, cross_origin


def _attribute_plan(member) -> dict:
    if int(member.callback_type) != CallbackType.kPy:
        # A JavaScript-backed member carries the *name* of a patch rather than a
        # callable, and a plan has nowhere to put one.  Writing the member without it
        # would install an interface that is subtly not what was declared, so the
        # patch is named in the refusal.
        raise UnsupportedMember(
            f"{member.name}: a JavaScript-backed attribute names the patch "
            f"{member.getter!r}, which this description cannot describe"
        )

    # The cross-origin flags read inverted from what they mean: the compiled value is
    # 0 for "check" and 1 for "do not check", so nearly every member carries both.
    # They are kept rather than dropped because they describe the declaration -- the
    # native side enforces the policy on the interface, since that is the granularity
    # V8 offers, and the member flag says which members asked for it.
    flags = []
    if int(member.receiver_check) == FlagReceiverCheck.kCheck:
        flags.append("receiver_check")
    if int(member.cross_origin_get_check) == FlagCrossOriginCheck.kCheck:
        flags.append("cross_origin_get")
    if int(member.cross_origin_set_check) == FlagCrossOriginCheck.kCheck:
        flags.append("cross_origin_set")
    if bool(getattr(member, "replaceable", False)):
        flags.append("replaceable")

    plan = {
        "kind": "attribute",
        "name": member.name,
        "location": _LOCATIONS[int(member.location)],
        "property_flags": int(member.property_flags),
        "side_effect": int(member.side_effect),
        "getter": member.getter,
    }
    setter = getattr(member, "setter", None)
    if setter is not None:
        flags.append("has_setter")
        plan["setter"] = setter
    plan["flags"] = flags
    return plan


def _method_plan(member) -> dict:
    if int(member.callback_type) != CallbackType.kPy:
        raise UnsupportedMember(
            f"{member.name}: a JavaScript-backed method names the patch "
            f"{member.callback!r}, which this description cannot describe"
        )

    flags = []
    if int(member.receiver_check) == FlagReceiverCheck.kCheck:
        flags.append("receiver_check")
    if int(member.cross_origin_check) == FlagCrossOriginCheck.kCheck:
        flags.append("cross_origin")

    return {
        "kind": "method",
        "name": member.name,
        "location": _LOCATIONS[int(member.location)],
        "property_flags": int(member.property_flags),
        "side_effect": int(member.side_effect),
        "arity": int(member.length),
        "flags": flags,
        "callback": member.callback,
    }


class InterfaceDescription:
    """Assembles one batch description, assigning a slot to each callable it is given.

    One instance is one install: the slots are indices into the single tuple the
    native reader takes, so callables from two batches must never share a builder.
    """

    def __init__(self):
        self._interfaces: list[dict] = []
        self._implementations: list = []
        self._lazy: list[str] = []
        self._lazy_factory = None
        self._sealed = False

    def _slot(self, callable) -> int:
        self._implementations.append(callable)
        return len(self._implementations) - 1

    # -- interfaces ----------------------------------------------------------

    def add(self, spec, *, name: str, implementation=None, parent: str | None = None) -> dict:
        """Compile a declaration into a plan and describe it, in one step."""
        return self.add_plan(
            plan_from_spec(spec, name=name, implementation=implementation, parent=parent)
        )

    def add_plan(self, plan: dict, *, exposed: bool = True) -> dict:
        """Describe one interface from a plan, and return its mapping.

        A plan is what the compiler produces and what a lazy factory answers with, so
        this is the one place that turns a compiled interface into the shape the
        native reader takes -- whether it was installed eagerly at realm creation or
        built on demand years later.

        ``exposed`` says whether installing it also puts its name on the global
        object.  An interface pulled in as a parent, or built because a Python
        object of its type crossed, is installed without becoming a name a page
        can read.
        """
        interface: dict = {"name": plan["name"], "members": []}

        for key in ("parent", "to_string_tag", "traits", "length", "constructor", "array"):
            value = plan.get(key)
            if value:
                interface[key] = value

        if not exposed:
            interface["exposed"] = False

        implementation = plan.get("type")
        if implementation is not None:
            interface["type"] = self._slot(implementation)

        adapter = plan.get("constructor_adapter")
        if adapter is not None:
            interface["constructor_slot"] = self._slot(
                bind_adapter(adapter, implementation, plan["name"])
            )

        call = plan.get("call")
        if call is not None:
            interface["call"] = self._slot(call)

        handlers = plan.get("handlers") or {}
        if handlers:
            interface["handlers"] = {
                role: self._slot(value) for role, value in handlers.items()
            }
        cross_origin = plan.get("cross_origin") or {}
        if cross_origin:
            interface["cross_origin"] = {
                role: self._slot(value) for role, value in cross_origin.items()
            }

        for member in plan.get("members", ()):
            interface["members"].append(self._member(member))

        self._interfaces.append(interface)
        return interface

    def _member(self, member: dict) -> dict:
        kind = member["kind"]
        entry: dict = {"kind": kind, "name": member["name"]}

        if kind == "constant":
            entry["value"] = int(member["value"])
            entry["location"] = "interface"
            return entry

        if kind == "constant_callback":
            # Interface location because a constant is a property of the constructor,
            # not of the objects it makes.
            entry["location"] = "interface"
            entry["slot"] = self._slot(member["callback"])
            return entry

        entry["location"] = member["location"]
        entry["property_flags"] = int(member.get("property_flags", 0))
        entry["side_effect"] = int(member.get("side_effect", 0))
        entry["flags"] = list(member.get("flags", ()))

        if kind == "attribute":
            entry["slot"] = self._slot(member["getter"])
            setter = member.get("setter")
            if setter is not None:
                entry["setter_slot"] = self._slot(setter)
        else:
            entry["arity"] = int(member.get("arity", 0))
            entry["slot"] = self._slot(member["callback"])
        return entry

    # -- lazy names ----------------------------------------------------------

    def declare_lazy(self, names, factory) -> None:
        """Expose names that are built the first time something reads them.

        The factory is called with a name and answers with a description for it, or
        ``None`` when the name is not this realm's.  That is the same shape
        :meth:`add_plan` builds, so a realm's lazy names and its eager ones go through
        one implementation of building an interface rather than two.
        """
        self._lazy.extend(str(name) for name in names)
        self._lazy_factory = factory

    # -- output --------------------------------------------------------------

    def description(self) -> dict:
        """The mapping to hand to ``Realm.install`` or ``Realm(...)``.

        Sealed on the way out: the tuple's indices are the interface, so a callable
        added afterwards would be one no member could reach.
        """
        if self._sealed:
            raise UnsupportedMember("this description has already been built")
        self._sealed = True

        payload: dict = {"interfaces": self._interfaces}
        if self._lazy:
            if self._lazy_factory is None:
                raise UnsupportedMember("lazy names need a factory to build them")
            payload["lazy"] = list(dict.fromkeys(self._lazy))
            # Taken before the tuple is built, not after: the factory is a callable
            # like any other and its index has to be one the tuple actually has.
            payload["lazy_factory"] = self._slot(self._lazy_factory)
        payload["implementations"] = tuple(self._implementations)
        return payload


def describe_plan(plan: dict) -> dict:
    """The description for one plan, which is what a lazy factory answers with.

    A factory is asked for one name at a time, so the batch it answers with holds one
    interface and whatever the reader already has -- which is why this is a function
    rather than a second builder class.
    """
    builder = InterfaceDescription()
    builder.add_plan(plan)
    return builder.description()


def describe_one(spec, *, name, implementation=None, parent=None) -> dict:
    """The description for one compiled declaration, for a lazy factory to answer with."""
    return describe_plan(
        plan_from_spec(spec, name=name, implementation=implementation, parent=parent)
    )


__all__ = [
    "InterfaceDescription",
    "UnsupportedMember",
    "bind_adapter",
    "describe_one",
    "describe_plan",
    "plan_from_spec",
]
