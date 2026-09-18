import gc
import weakref
from collections import Counter

import pytest

from pyv8c.engine.universe import BaselineRepository, InterfaceUniverse
from pyv8c.platform.bootstrap import bootstrap_runtime


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
    first = universe.plan_for("Element", object())
    parameters = dict(compiler.wrappers._parameter_cache._values)
    for _ in range(4):
        assert universe.plan_for("Element", object()) is first
    assert all(count == 1 for count in calls.values())
    assert compiler.wrappers._parameter_cache._values == parameters


def test_a_realm_description_covers_ancestors_and_is_pure(universe):
    """Building a description installs nothing, so a failed realm leaves no trace.

    The realm's own batch is a construction parameter: if creating the realm
    fails, the description has to be buildable again, which is why the session
    records a name only once the realm that installed it exists.
    """
    session = universe.binding_session()
    batch = session.realm_description("Window")

    assert {"EventTarget", "Node", "Element"} <= set(batch.names)
    # Ancestors come first, so a failure names the interface that was wrong
    # rather than the child that referred to it.
    assert batch.names.index("Node") < batch.names.index("Element")

    # Nothing is installed yet, and rebuilding gives the same answer.
    assert session.materialized_interfaces == ()
    again = session.realm_description("Window")
    assert again.names == batch.names

    class Native:
        def __init__(self):
            self.installs = []

        def install(self, description):
            self.installs.append(description)

    native = Native()
    session.attach_realm(native, batch.names)
    assert set(session.materialized_interfaces) >= {"EventTarget", "Node", "Element"}

    # Materializing a name the realm already has installs nothing.
    session.materialize("Element")
    assert native.installs == []


def test_a_plan_is_compiled_once_and_bound_per_realm(universe):
    # A plan is what a realm installs, and it carries callables rather than slots, so
    # one page's interface table is compiled once and installed into every realm.
    # Pinning the identity is what says "once": a plan rebuilt per call would install
    # just as correctly and would still re-derive every interface for every page.
    compiler = universe._assembly

    shared = compiler.plan("URL")
    assert shared is compiler.plan("URL")
    assert shared["name"] == "URL"
    assert isinstance(shared["type"], type), "the Python class is the interface's identity"

    # A parent travels as a name, because the description is read before any template
    # exists and so cannot refer to one.
    assert compiler.plan("Element")["parent"] == "Node"

    # A static member that asked for the realm is the one thing that depends on it, so
    # it is re-bound into a copy -- and never cached, because a cache keyed by realm
    # would hold every page ever built.
    plain = {member["name"]: member.get("callback")
             for member in shared["members"] if member["kind"] == "method"}

    first = compiler.plan("URL", object())
    second = compiler.plan("URL", object())
    assert first is not shared
    assert first is not second

    first_methods = {member["name"]: member.get("callback")
                     for member in first["members"] if member["kind"] == "method"}
    second_methods = {member["name"]: member.get("callback")
                      for member in second["members"] if member["kind"] == "method"}
    assert first_methods["createObjectURL"] is not second_methods["createObjectURL"]
    assert first_methods["canParse"] is plain["canParse"]

    # Binding copied: the shared plan is what every unbound realm will read.
    assert {member["name"]: member.get("callback")
            for member in shared["members"] if member["kind"] == "method"} == plain


def test_event_handler_slots_follow_the_baseline_member_table(universe):
    """Each interface compiles exactly the ``on*`` slots its baseline declares."""
    event_target = universe.class_for("EventTarget")
    names = [item.name for item in event_target.__v8_idl_spec__.attributes]
    assert len(names) == len(set(names))
    assert {"onload", "onerror", "onclick", "onkeydown", "onhashchange"} <= set(names)
    for slot in ("onclick", "onkeydown"):
        assert event_target.__dict__["get_" + slot].__name__ == "get_" + slot
        assert event_target.__dict__["set_" + slot].__name__ == "set_" + slot

    window = _attributes(universe, "Window")
    assert {"onclick", "onkeydown", "onwheel", "onhashchange", "onpopstate", "onstorage"} <= set(window)
    assert window["onclick"]["getter"].__name__ == "get_onclick"

    document = set(_attributes(universe, "Document"))
    assert {"onclick", "onvisibilitychange", "onfreeze", "onresume"} <= document

    # Interfaces whose baseline member table has no such slot never grow one.
    node = set(_attributes(universe, "Node"))
    assert not ({"onclick", "onkeydown", "onhashchange", "onvisibilitychange"} & node)


def _attributes(universe, name):
    """The plan's attribute members, keyed by name.

    A plan refers to its callables directly rather than by slot -- the batch
    assigns those -- so the getter is readable here without installing anything.
    """
    return {
        member["name"]: member
        for member in universe.plan_for(name)["members"]
        if member["kind"] == "attribute"
    }


def test_realm_dependent_callbacks_are_isolated_and_released(universe):
    class Realm:
        pass

    first_realm, second_realm = Realm(), Realm()
    reference = weakref.ref(first_realm)

    def methods(realm):
        return {member["name"]: member.get("callback")
                for member in universe.plan_for("URL", realm)["members"]
                if member["kind"] == "method"}

    first_methods = methods(first_realm)
    second_methods = methods(second_realm)
    assert first_methods["createObjectURL"] is not second_methods["createObjectURL"]
    assert first_methods["canParse"] is second_methods["canParse"]

    # The realm-bound copy is not cached -- a cache keyed by realm would hold
    # every page ever built -- so nothing keeps the realm alive.
    del first_methods, first_realm
    gc.collect()
    assert reference() is None


def test_strict_validation_is_a_single_cached_audit(universe, monkeypatch):
    universe.validate()
    before = dict(universe._assembly._compiled)

    def unexpected(name):
        raise AssertionError(name)

    monkeypatch.setattr(universe._assembly, "_compile", unexpected)
    universe.validate()
    universe.plan_for("Element")
    assert universe._assembly._compiled == before


def test_duplicate_declarations_fail_before_a_realm_can_install_them(universe, monkeypatch):
    cls = universe.class_for("Node")
    declaration = cls.__v8_idl_spec__
    duplicate = declaration.methods[0]
    monkeypatch.setattr(cls, "__v8_idl_spec__", declaration.replace(methods=(duplicate, duplicate)))
    with pytest.raises(TypeError, match="duplicate methods"):
        universe.plan_for("Node")


def test_a_javascript_backed_member_is_refused_rather_than_planned(universe, monkeypatch):
    # pyv8c once patched some members from JavaScript: a kJs member carries the
    # name of a patch rather than a callable, and a description has nowhere to
    # put one.  Planning the member without it would install an interface that
    # is subtly not what was declared, so it is refused by name instead.
    from v8n import CallbackType

    from pyv8c.engine.description import UnsupportedMember

    cls = universe.class_for("Node")
    declaration = cls.__v8_idl_spec__
    method = declaration.methods[0].replace(callback="fakerNodeMethod", callback_type=CallbackType.kJs)
    monkeypatch.setattr(cls, "__v8_idl_spec__", declaration.replace(methods=(method,) + declaration.methods[1:]))
    with pytest.raises(UnsupportedMember, match="fakerNodeMethod"):
        universe.plan_for("Node")
