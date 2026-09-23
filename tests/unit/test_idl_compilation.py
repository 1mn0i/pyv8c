import gc
import weakref
from collections import Counter

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c.engine.universe import BaselineRepository, InterfaceUniverse  # noqa: E402
from pyv8c.platform.bootstrap import bootstrap_runtime  # noqa: E402


@pytest.fixture
def universe():
    return InterfaceUniverse(BaselineRepository.load(), runtime=bootstrap_runtime(), stub_logging="off")


def test_compilation_and_signature_analysis_are_not_repeated(universe, monkeypatch):
    compiler = universe._assembly
    calls = Counter()
    original = compiler._compile

    def compile_once(name):
        calls[name] += 1
        return original(name)

    monkeypatch.setattr(compiler, "_compile", compile_once)
    first = universe.binding_session(object()).class_config_for("Element")
    parameters = dict(compiler.wrappers._parameter_cache._values)
    for _ in range(4):
        assert universe.binding_session(object()).class_config_for("Element") is first
    assert all(count == 1 for count in calls.values())
    assert compiler.wrappers._parameter_cache._values == parameters


def test_descendant_lookup_is_computed_once(universe):
    catalog = universe._catalog
    first = catalog.descendants("EventTarget")
    assert isinstance(first, tuple)
    # The same answer object, not an equal rebuild: one member lookup walks
    # ancestors, and each of them asks for the same descendant set again.
    assert catalog.descendants("EventTarget") is first
    assert "Element" in first


def test_session_commits_ancestors_atomically_and_can_retry(universe):
    session = universe.binding_session()
    batches = []

    class Context:
        failing = True

        def update_configs(self, configs):
            batches.append(configs)
            if self.failing:
                raise RuntimeError("native commit failed")

    context = Context()
    session.attach_context(context)
    with pytest.raises(RuntimeError, match="native commit failed"):
        session.materialize("Element")
    assert not session.configs
    assert session.materialized_interfaces == ()
    context.failing = False
    session.materialize("Element")
    assert set(session.materialized_interfaces) == {"EventTarget", "Node", "Element"}
    assert set(batches[-1]) == {universe.class_for(name) for name in session.materialized_interfaces}
    session.materialize("Element")
    assert len(batches) == 2


def test_event_handler_slots_follow_the_baseline_member_table(universe):
    """Each interface compiles exactly the ``on*`` slots its baseline declares."""
    event_target = universe.class_for("EventTarget")
    names = [item.name for item in event_target.__v8_idl_spec__.attributes]
    assert len(names) == len(set(names))
    assert {"onload", "onerror", "onclick", "onkeydown", "onhashchange"} <= set(names)
    for slot in ("onclick", "onkeydown"):
        assert event_target.__dict__["get_" + slot].__name__ == "get_" + slot
        assert event_target.__dict__["set_" + slot].__name__ == "set_" + slot

    window = {item.name: item for item in universe.class_config_for("Window").attributes}
    assert {"onclick", "onkeydown", "onwheel", "onhashchange", "onpopstate", "onstorage"} <= set(window)
    assert window["onclick"].getter.__name__ == "get_onclick"

    document = {item.name for item in universe.class_config_for("Document").attributes}
    assert {"onclick", "onvisibilitychange", "onfreeze", "onresume"} <= document

    # Interfaces whose baseline member table has no such slot never grow one.
    node = {item.name for item in universe.class_config_for("Node").attributes}
    assert not ({"onclick", "onkeydown", "onhashchange", "onvisibilitychange"} & node)


def test_realm_dependent_callbacks_are_isolated_and_released(universe):
    class Realm:
        pass

    first_realm, second_realm = Realm(), Realm()
    reference = weakref.ref(first_realm)
    first = universe.binding_session(first_realm)
    second = universe.binding_session(second_realm)
    first_spec = first.class_config_for("URL")
    second_spec = second.class_config_for("URL")
    assert first_spec is not second_spec
    first_methods = {member.name: member.callback for member in first_spec.methods}
    second_methods = {member.name: member.callback for member in second_spec.methods}
    assert first_methods["createObjectURL"] is not second_methods["createObjectURL"]
    assert first_methods["canParse"] is second_methods["canParse"]
    del first, first_spec, first_methods, first_realm
    gc.collect()
    assert reference() is None


def test_strict_validation_is_a_single_cached_audit(universe, monkeypatch):
    universe.validate()
    before = dict(universe._assembly._compiled)

    def unexpected(name):
        raise AssertionError(name)

    monkeypatch.setattr(universe._assembly, "_compile", unexpected)
    universe.validate()
    universe.binding_session().materialize("Element")
    assert universe._assembly._compiled == before


def test_duplicate_declarations_fail_before_session_commit(universe, monkeypatch):
    cls = universe.class_for("Node")
    declaration = cls.__v8_idl_spec__
    duplicate = declaration.methods[0]
    monkeypatch.setattr(cls, "__v8_idl_spec__", declaration.replace(methods=(duplicate, duplicate)))
    session = universe.binding_session()
    with pytest.raises(TypeError, match="duplicate methods"):
        session.materialize("Node")
    assert not session.configs


