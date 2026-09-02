"""Behavioural guarantees of the pre-compiled Web IDL binding plans.

The hot paths in ``bindings.webidl`` cache work at compile time (required
argument counts, overload plans, scalar dispatch). These tests pin the
observable behaviour so the caches cannot silently change semantics.
"""

import pytest

from pyv8c.engine.webidl import (
    BindingPlan,
    MethodSpec,
    ParamSpec,
    compile_binding,
    compile_signature,
    validate_arguments,
)


def _plan(signature, name="method", interface="Widget"):
    return compile_binding(interface, name, "operation", signature)


def test_required_count_is_precomputed_and_matches_the_scan():
    spec = compile_signature({
        "status": "generated",
        "parameters": [
            {"type": "IDLString"},
            {"type": "IDLString", "optional": True},
            {"type": "IDLString", "variadic": True},
        ],
    })
    assert spec.required_arg_count == 1
    assert spec.required_count() == 1
    assert spec.required_count() is spec.required_arg_count


def test_explicit_required_overrides_the_scan():
    spec = compile_signature({
        "status": "generated",
        "required": 2,
        "parameters": [
            {"type": "IDLString", "optional": True},
            {"type": "IDLString", "optional": True},
        ],
    })
    assert spec.required_count() == 2


def test_required_count_survives_direct_construction():
    """Constructing a MethodSpec by hand must still compute the count."""
    spec = MethodSpec(
        (ParamSpec("DOMString"), ParamSpec("DOMString", optional=True)),
    )
    assert spec.required_count() == 1


def test_overload_plans_are_built_once():
    plan = _plan({
        "status": "generated",
        "overloads": [
            {"status": "generated", "parameters": [{"type": "IDLString"}]},
            {"status": "generated", "parameters": [{"type": "IDLDouble"}]},
        ],
    })
    assert len(plan.overload_plans) == 2
    assert all(isinstance(item, BindingPlan) for item in plan.overload_plans)
    # Each overload carries the identity of the plan it came from.
    assert {item.name for item in plan.overload_plans} == {"method"}
    assert plan.overload_plans[0].signature is not plan.signature


def test_overloads_pick_the_matching_branch():
    plan = _plan({
        "status": "generated",
        "overloads": [
            {"status": "generated", "parameters": [{"type": "IDLDouble"}]},
            {"status": "generated", "parameters": [{"type": "IDLString"}]},
        ],
    })
    assert validate_arguments(plan, (7,)) == (7.0,)
    # The double branch rejects text, so the string overload handles it.
    assert validate_arguments(plan, ("text",)) == ("text",)


def test_overload_failure_reports_no_matching_signature():
    plan = _plan({
        "status": "generated",
        "overloads": [
            {"status": "generated", "parameters": [{"type": "IDLLong"}]},
            {"status": "generated", "parameters": [{"type": "IDLByteString"}]},
        ],
    })
    with pytest.raises(Exception, match="no matching overload"):
        validate_arguments(plan, ("中",))


def test_plan_without_overloads_has_no_overload_plans():
    plan = _plan({"status": "generated", "parameters": [{"type": "IDLString"}]})
    assert plan.overload_plans == ()
    assert validate_arguments(plan, ("ok",)) == ("ok",)


def test_scalar_fast_path_still_enforces_byte_string_bounds():
    plan = _plan({
        "status": "generated",
        "parameters": [{"type": "IDLByteString"}],
    })
    assert validate_arguments(plan, ("plain",)) == ("plain",)
    # U+4E2D is outside Latin-1, so ByteString must reject it.
    with pytest.raises(Exception, match="ByteString"):
        validate_arguments(plan, ("中",))


def test_enum_values_are_still_validated():
    """The scalar fast path must not bypass enum checks."""
    plan = _plan({
        "status": "generated",
        "parameters": [{"type": "IDLString", "enum": ["a", "b"]}],
    })
    assert validate_arguments(plan, ("a",)) == ("a",)
    with pytest.raises(Exception, match="enum"):
        validate_arguments(plan, ("c",))


def test_union_prefers_the_sequence_branch_for_lists():
    plan = _plan({
        "status": "generated",
        "parameters": [{
            "type": {
                "kind": "union",
                "members": ["DOMString", {"kind": "sequence", "item": "DOMString"}],
            },
        }],
    })
    # Lists prefer the sequence branch even when DOMString is listed first.
    assert validate_arguments(plan, (["a", "b"],)) == (["a", "b"],)
    assert validate_arguments(plan, ("text",)) == ("text",)


def test_union_failure_reports_no_permitted_type():
    plan = _plan({
        "status": "generated",
        "parameters": [{"type": {"kind": "union", "members": ["IDLByteString", "IDLLong"]}}],
    })
    with pytest.raises(Exception, match="does not match any permitted type"):
        validate_arguments(plan, ("中",))


def test_integer_conversion_still_ranges_checks():
    plan = _plan({
        "status": "generated",
        "parameters": [{"type": "IDLLong"}],
    })
    assert validate_arguments(plan, ("12",)) == (12,)
    with pytest.raises(Exception):
        validate_arguments(plan, (2 ** 40,))


def test_too_many_and_too_few_arguments_still_raise():
    plan = _plan({
        "status": "generated",
        "max_args": 2,
        "parameters": [{"type": "IDLString"}, {"type": "IDLString", "optional": True}],
    })
    with pytest.raises(Exception, match="argument\\(s\\) required"):
        validate_arguments(plan, ())
    with pytest.raises(Exception, match="too many arguments"):
        validate_arguments(plan, ("a", "b", "c"))
