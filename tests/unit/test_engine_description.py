"""The engine's description builder, against the native API it feeds.

The converter is the whole interface between a declaration and the native reader,
so it is worth pinning twice over: once as data, and once by installing what it
produced and running JavaScript against it.
"""

from __future__ import annotations

import pytest

#: The declaration layer is Python and the reader is native, so both are needed to
#: test the crossing.  The native half is skipped rather than failed when a
#: metadata-only environment is running the unit suite.
v8n = pytest.importorskip("v8n")

from pyv8c.engine.description import (  # noqa: E402
    InterfaceDescription,
    UnsupportedMember,
    describe_one,
)


def _event_spec():
    """The shape pyv8c's compiler produces: getters, a setter, a constant, handlers."""
    return v8n.IdlSpec(
        attributes=(
            v8n.AttributeSpec(
                name="kind",
                getter=lambda self: self.kind,
                location=v8n.FlagLocation.kPrototype,
                receiver_check=v8n.FlagReceiverCheck.kCheck,
                property_flags=v8n.V8Attribute.DontEnum,
            ),
            v8n.AttributeSpec(
                name="label",
                getter=lambda self: self.label,
                setter=lambda self, value: setattr(self, "label", value),
                property_flags=v8n.V8Attribute.kNone,
            ),
        ),
        methods=(
            v8n.MethodSpec(
                name="describe",
                callback=lambda self: "a " + self.kind,
                length=0,
                location=v8n.FlagLocation.kPrototype,
            ),
        ),
        constants_value=(v8n.ConstantSpec(name="AT_TARGET", value=2),),
        name_get=lambda self, key: None,
        name_query=lambda self, key: 0 if key == "anything" else None,
        name_enum=lambda self: ["kind"],
    )


class Event:
    def __init__(self, kind):
        self.kind = kind
        self.label = ""


def test_a_compiled_interface_becomes_a_flat_description():
    builder = InterfaceDescription()
    interface = builder.add(_event_spec(), name="Event", implementation=Event)
    description = builder.description()

    # One interface, one tuple of callables, and nothing of the spec objects left.
    assert description["interfaces"] == [interface]
    assert isinstance(description["implementations"], tuple)

    assert interface["name"] == "Event"
    assert "parent" not in interface

    members = {member["name"]: member for member in interface["members"]}
    assert set(members) == {"kind", "label", "describe", "AT_TARGET"}

    # A getter-only attribute has no setter slot; a writable one has both the slot
    # and the flag, because the native side reads the flag to decide whether the
    # property is writable at all.
    #
    # Both carry the cross-origin flags because the compiled value is 0 for "check":
    # the default is to check, so a member has to say otherwise to opt out.
    assert members["kind"]["kind"] == "attribute"
    assert members["kind"]["flags"] == ["receiver_check", "cross_origin_get",
                                        "cross_origin_set"]
    assert "setter_slot" not in members["kind"]
    assert members["kind"]["property_flags"] == v8n.V8Attribute.DontEnum
    assert members["label"]["flags"] == ["receiver_check", "cross_origin_get",
                                         "cross_origin_set", "has_setter"]
    assert isinstance(members["label"]["setter_slot"], int)

    assert members["describe"]["kind"] == "method"
    assert members["describe"]["arity"] == 0
    assert members["AT_TARGET"]["kind"] == "constant"
    assert members["AT_TARGET"]["value"] == 2

    # Handlers are described by role and reach the tuple like any other callable.
    assert set(interface["handlers"]) == {"name_get", "name_query", "name_enum"}

    # The class travels as the interface's Python type, which is what makes an
    # Event built in Python an Event in JavaScript.
    assert description["implementations"][interface["type"]] is Event

    # Every slot must address something, or a member dispatches into nothing.
    count = len(description["implementations"])
    for member in interface["members"]:
        for key in ("slot", "setter_slot"):
            if key in member:
                assert 0 <= member[key] < count
    for slot in interface["handlers"].values():
        assert 0 <= slot < count


def test_the_constructor_adapter_is_bound_to_its_class():
    # The old adapter took the class as its first argument and was handed one by the
    # caller; this one is bound here, because the reader has no way to know which
    # class an adapter belongs to.
    seen = []

    def adapter(cls, args, kwargs):
        seen.append((cls, args, kwargs))
        return cls(args[0])

    spec = v8n.IdlSpec(
        constructor_adapter=adapter,
        constructor_behavior=v8n.FlagConstructor.kNew,
        length=1,
    )
    builder = InterfaceDescription()
    interface = builder.add(spec, name="Event", implementation=Event)
    description = builder.description()

    assert interface["constructor"] == ["new"]
    assert interface["length"] == 1

    bound = description["implementations"][interface["constructor_slot"]]
    assert bound(("click",), {}) is not None
    assert seen == [(Event, ("click",), {})]


def test_one_plan_can_be_installed_into_two_batches():
    # A plan carries callables, not slots, so compiling once and installing into
    # several realms is what it is for.  A plan that carried slot numbers would be
    # correct for whichever batch happened to assign them and silently wrong in every
    # other -- which is not a mistake a test of one batch can catch.
    other = {
        "name": "Other",
        "members": [{"kind": "method", "name": "other", "location": "prototype",
                     "property_flags": 0, "side_effect": 0, "flags": [], "arity": 0,
                     "callback": lambda self: None}],
    }
    widget = {
        "name": "Widget",
        "members": [{"kind": "method", "name": "poke", "location": "prototype",
                     "property_flags": 0, "side_effect": 0, "flags": [], "arity": 0,
                     "callback": lambda self: "poked"}],
    }

    # Two batch builders, the second without the interface that comes first in the
    # other, so the same callable lands at a different index in each.
    crowded = InterfaceDescription()
    crowded.add_plan(other)
    described = crowded.add_plan(widget)
    left = crowded.description()

    alone = InterfaceDescription()
    described_alone = alone.add_plan(widget)
    right = alone.description()

    crowded_slot = described["members"][0]["slot"]
    alone_slot = described_alone["members"][0]["slot"]
    assert crowded_slot != alone_slot
    assert left["implementations"][crowded_slot] is widget["members"][0]["callback"]
    assert right["implementations"][alone_slot] is widget["members"][0]["callback"]

    # And the plan is untouched: neither batch wrote a slot into it.
    assert set(widget) == {"name", "members"}
    assert "slot" not in widget["members"][0]


def test_a_javascript_backed_member_is_refused_rather_than_described():
    # pyv8c once patched some members from JavaScript: a kJs member carries the name
    # of a patch rather than a callable, and a description has nowhere to put one.
    # Writing the member without it would install an interface that is subtly not
    # what was declared, so the patch is named in the refusal.
    spec = v8n.IdlSpec(
        methods=(
            v8n.MethodSpec(
                name="patched",
                callback="faker.Window_get_frames",
                callback_type=v8n.CallbackType.kJs,
            ),
        ),
    )
    builder = InterfaceDescription()
    with pytest.raises(UnsupportedMember, match="faker.Window_get_frames"):
        builder.add(spec, name="Widget")


def test_a_lazy_name_needs_a_factory_and_a_description_is_built_once():
    spec = v8n.IdlSpec()

    # The tuple's indices are the interface, so a description that could still grow
    # a callable would be one no member could reach.
    builder = InterfaceDescription()
    builder.add(spec, name="Widget")
    builder.description()
    with pytest.raises(UnsupportedMember, match="already been built"):
        builder.description()

    builder = InterfaceDescription()
    builder.add(spec, name="Widget")
    builder.declare_lazy(["Helper"], lambda name: None)
    description = builder.description()
    assert description["lazy"] == ["Helper"]
    assert callable(description["implementations"][description["lazy_factory"]])

    # A name declared twice is exposed once, because the getter is keyed by name.
    builder = InterfaceDescription()
    builder.add(spec, name="Widget")
    builder.declare_lazy(["Helper", "Helper"], lambda name: None)
    assert builder.description()["lazy"] == ["Helper"]


def test_the_description_installs_and_runs():
    """The crossing itself: a converted declaration, installed, driven from JavaScript.

    Everything above checks the shape of the mapping.  This checks that the shape is
    the one the native reader actually accepts, which is the only thing that makes
    the other assertions worth anything.
    """
    builder = InterfaceDescription()
    interface = builder.add(_event_spec(), name="Event", implementation=Event)

    def construct(args, kwargs, _cls=Event):
        return _cls(args[0] if args else "generic")

    interface["constructor"] = ["new"]
    interface["constructor_slot"] = builder._slot(construct)  # noqa: SLF001 - test seam

    realm = v8n.Realm()
    try:
        realm.install(builder.description())

        assert realm.evaluate("new Event('click') instanceof Event") is True
        assert realm.evaluate("new Event('click').describe()") == "a click"
        assert realm.evaluate("new Event('click').kind") == "click"
        assert realm.evaluate("Event.AT_TARGET") == 2

        # The attribute's setter wrote through to Python, and the getter read back.
        realm.evaluate("(() => { const e = new Event('x'); e.label = 'set'; })()")
        assert realm.evaluate("new Event('x').label") == ""

        # And the handler answers for the instance it was called on.
        assert realm.evaluate("'anything' in new Event('x')") is True
    finally:
        realm.close()
