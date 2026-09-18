"""The declaration layer: what a host writes about an interface.

A declaration is data now rather than a native object, so the properties worth
pinning are the ones a compiler depends on: that the defaults are the ones the
old binding had, that ``replace`` re-derives without touching the original, and
that a declaration's callables reach the batch reader unaltered.
"""

from __future__ import annotations

import pytest

import v8n


def test_attribute_defaults_match_the_native_spec():
    attribute = v8n.AttributeSpec(name="kind", getter="get_kind")

    # Location defaults to the prototype, which is where nearly every member
    # lives; the interface location is the exception and says so.
    assert v8n.FlagLocation.kPrototype == attribute.location
    # The cross-origin flags read inverted: zero is "run the check", so a
    # declaration that says nothing is checked rather than unchecked.
    assert attribute.cross_origin_get_check == v8n.FlagCrossOriginCheck.kCheck
    assert attribute.cross_origin_set_check == v8n.FlagCrossOriginCheck.kCheck
    assert attribute.receiver_check == v8n.FlagReceiverCheck.kCheck
    assert attribute.setter is None
    assert attribute.replaceable is False
    assert attribute.callback_type == v8n.CallbackType.kPy


def test_method_defaults_match_the_native_spec():
    method = v8n.MethodSpec(name="bump", callback="bump")

    assert method.length == 0
    assert method.location == v8n.FlagLocation.kPrototype
    assert method.cross_origin_check == v8n.FlagCrossOriginCheck.kCheck
    assert method.side_effect == v8n.SideEffectType.kHasSideEffect


def test_a_spec_defaults_to_an_empty_namespace():
    spec = v8n.IdlSpec()

    assert spec.attributes == ()
    assert spec.methods == ()
    assert spec.constants_value == ()
    assert spec.constructor_behavior == v8n.FlagConstructor.kDontAll
    assert spec.call is None
    assert spec.name_get is None


def test_replace_returns_a_new_declaration():
    # The compiler re-derives a declaration when one member needs a bound
    # callback; the shared one has to survive that, because it is what every
    # other realm installs.
    original = v8n.MethodSpec(name="poke", callback="poke")
    bound = original.replace(callback=lambda self: "poked", location=v8n.FlagLocation.kInterface)

    assert original.callback == "poke"
    assert original.location == v8n.FlagLocation.kPrototype
    assert callable(bound.callback)
    assert bound.location == v8n.FlagLocation.kInterface
    assert bound.name == original.name


@pytest.mark.parametrize(
    "spec",
    [
        v8n.AttributeSpec(name="kind", getter="get_kind"),
        v8n.MethodSpec(name="poke", callback="poke"),
        v8n.ConstantSpec(name="LIMIT", value=7),
        v8n.ConstantCallbackSpec(name="COUNT", callback="count"),
        v8n.IdlSpec(),
    ],
)
def test_a_declaration_can_be_a_dictionary_key(spec):
    # Declarations are cache keys, so identity hashing is the behaviour that
    # matters: two interfaces may declare identical members and must still be
    # two entries.
    assert {spec: spec}[spec] is spec


def test_a_declaration_installs_through_the_description_reader():
    # The seam that matters: a declaration is only useful if the values it holds
    # arrive at the batch reader unchanged.
    spec = v8n.IdlSpec(
        length=1,
        constructor_behavior=v8n.FlagConstructor.kNew,
        has_constructor=1,
        to_string_tag="Counter",
        attributes=(
            v8n.AttributeSpec(
                name="value",
                getter=lambda self: self.value,
                setter=lambda self, value: setattr(self, "value", value),
                property_flags=v8n.V8Attribute.DontEnum,
            ),
        ),
        methods=(v8n.MethodSpec(name="bump", callback="bump", length=1),),
        constants_value=(v8n.ConstantSpec(name="LIMIT", value=7),),
    )

    class Counter:
        def __init__(self, value=0):
            self.value = value

    description = {
        "interfaces": [
            {
                "name": "Counter",
                "length": spec.length,
                "constructor": ["new"],
                "to_string_tag": spec.to_string_tag,
                "constructor_slot": 0,
                "members": [
                    {
                        "kind": "attribute",
                        "name": spec.attributes[0].name,
                        "slot": 1,
                        "setter_slot": 2,
                        "flags": ["has_setter"],
                        "property_flags": spec.attributes[0].property_flags,
                    },
                    {
                        "kind": "method",
                        "name": spec.methods[0].name,
                        "arity": spec.methods[0].length,
                        "slot": 3,
                    },
                    {
                        "kind": "constant",
                        "name": spec.constants_value[0].name,
                        "value": spec.constants_value[0].value,
                    },
                ],
            }
        ],
        "implementations": (
            lambda args, kwargs: Counter(args[0] if args else 0),
            spec.attributes[0].getter,
            spec.attributes[0].setter,
            lambda self, by: setattr(self, "value", self.value + by),
        ),
    }

    realm = v8n.Realm()
    try:
        realm.install(description)
        assert realm.evaluate("Counter.length") == 1
        assert realm.evaluate("(() => { const c = new Counter(3); c.bump(4); return c.value; })()") == 7
        assert realm.evaluate("Counter.prototype.LIMIT") == 7
        assert realm.evaluate("Object.keys(new Counter(1)).length") == 0
    finally:
        realm.close()


def test_trace_formatting_names_the_event_and_the_member():
    message = v8n.format_trace("get", "document", scope="Window")
    assert message == "[Window] getter: document"

    # Noise is suppressed by name, and a member that starts with an underscore
    # is internal by convention.
    assert v8n.format_trace("get", "console") is None
    assert v8n.format_trace("call", "_private") is None
