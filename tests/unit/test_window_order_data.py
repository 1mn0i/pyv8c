"""The Window order document, and the plan it turns into.

The order is data, so these cases read it the way a Browser does: from the
shipped file, through the same decoder, and through ``build_plan``.  They run
without the native extension, like every other case in this directory.
"""
import json

import pytest

from pyv8c.engine.idl import Idl, build_plan
from pyv8c.engine.window_order import DEFAULT_ORDER_PATH, GROUPS, decode, load


def _document():
    return json.loads(DEFAULT_ORDER_PATH.read_text(encoding="utf-8"))


def test_the_shipped_order_names_the_baseline_release():
    order = load()
    assert order is not None
    assert order.chromium_version == Idl.load().metadata["chromium_version"]


def test_the_two_constructor_blocks_are_disjoint_and_ordered():
    order = load()
    assert len(order.constructors_before) == 610
    assert len(order.constructors_after) == 242
    assert len(order.constructors_after_late) == 64
    assert order.constructors_before == tuple(sorted(order.constructors_before, reverse=True))
    # The conditional block follows the reference browser's own install order,
    # which is not the name order.
    assert len(set(order.constructors_after)) == len(order.constructors_after)
    assert len(set(order.constructors_after_late)) == len(order.constructors_after_late)
    assert not set(order.constructors_before) & set(order.constructors_after)


def test_the_legacy_factories_keep_blinks_order():
    assert load().legacy_factories == (
        "Option", "Image", "Audio",
        "webkitURL", "webkitRTCPeerConnection", "webkitMediaStream",
        "WebKitMutationObserver", "WebKitCSSMatrix", "FeaturePolicy",
    )


def test_the_member_groups_cover_the_whole_window_interface():
    order = load()
    assert tuple(order.members) == GROUPS
    assert sum(len(group) for group in order.members.values()) == 264
    # The first attribute Chrome installs, and the context-dependent tail.
    assert order.group("early")[0] == "window"
    assert "caches" in order.group("late_attributes")
    assert "cookieStore" in order.group("late_attributes")
    assert "documentPictureInPicture" in order.group("late_attributes")
    assert "fetchLater" in order.group("late_operations")
    assert "originAgentCluster" in order.group("late_template")
    assert "requestResize" in order.group("tail")


def test_the_plan_splits_the_installs_in_blink_order():
    idl = Idl.load()
    plan = build_plan(idl, "Window")
    order = load()
    assert plan.legacy_factories == order.legacy_factories
    assert plan.member_groups == GROUPS
    assert len(plan.preinstall) == len(order.constructors_before)
    assert len(plan.postinstall) == len(order.constructors_after)
    assert len(plan.postinstall_late) == len(order.constructors_after_late)
    assert (set(plan.preinstall) | set(plan.postinstall)
            | set(plan.postinstall_late)) == set(plan.installs)
    assert not set(plan.preinstall) & set(plan.postinstall)


def test_a_worker_plan_keeps_the_document_order():
    plan = build_plan(Idl.load(), "Worker")
    assert plan.preinstall == ()
    assert plan.postinstall == ()
    assert plan.legacy_factories == ()
    assert plan.member_groups == ()


def test_a_document_without_the_order_keeps_the_old_order():
    idl = Idl.load()
    saved = idl.window_order
    try:
        idl.window_order = None
        plan = build_plan(idl, "Window")
        assert plan.preinstall == ()
        assert plan.postinstall == ()
        assert plan.member_groups == ()
        assert plan.installs == tuple(sorted(plan.installs))
    finally:
        idl.window_order = saved


def test_another_release_is_a_difference_the_bindings_can_report():
    document = _document()
    document["chromium_version"] = "155.0.0.0"
    assert decode(document).chromium_version == "155.0.0.0"


def test_a_block_out_of_order_is_refused_by_name():
    document = _document()
    document["constructors_before"] = list(reversed(document["constructors_before"]))
    with pytest.raises(ValueError, match="constructors_before"):
        decode(document)


def test_a_repeated_name_is_refused_by_name():
    document = _document()
    document["constructors_after"] = [*document["constructors_after"],
                                      document["constructors_after"][0]]
    with pytest.raises(ValueError, match="constructors_after"):
        decode(document)


def test_a_document_without_a_member_group_is_refused_by_name():
    document = _document()
    document["members"].pop("late_attributes")
    with pytest.raises(ValueError, match="members.late"):
        decode(document)


def test_cross_group_duplicates_are_rejected():
    document = _document()
    document["constructors_after_late"].append(document["constructors_after"][0])
    with pytest.raises(ValueError, match="across groups"):
        decode(document)


def test_missing_and_corrupt_order_files_are_distinct(tmp_path):
    assert load(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        load(broken)


def test_reference_reproduces_the_shipped_order():
    from pyv8c.tools.extract_window_order import build, DEFAULT_REFERENCE
    idl = Idl.load()
    reference = json.loads(DEFAULT_REFERENCE.read_text(encoding="utf-8"))
    conditional = set(reference["constructors_after"] + reference["constructors_after_late"])
    # A synthetic declaration map supplies the same conditional membership as the IDL.
    attrs = {name: ("[SecureContext]" if name in conditional else "")
             for name in idl.exposed("Window")}
    assert build(idl, attrs, idl.metadata["chromium_version"], reference) == _document()
    reference["constructors_after_late"].pop()
    with pytest.raises(ValueError, match="differs from IDL"):
        build(idl, attrs, idl.metadata["chromium_version"], reference)


def test_wrong_version_and_corrupt_data_fall_back_with_diagnostics(monkeypatch):
    from dataclasses import replace
    import pyv8c.engine.idl as module
    idl = Idl.load()
    monkeypatch.setattr(module, "load_window_order", lambda: replace(load(), chromium_version="0.0.0.0"))
    order, problem = idl._load_window_order()
    assert order is None and "this collection" in problem
    def corrupt():
        raise ValueError("invalid order")
    monkeypatch.setattr(module, "load_window_order", corrupt)
    order, problem = idl._load_window_order()
    assert order is None and "invalid order" in problem
