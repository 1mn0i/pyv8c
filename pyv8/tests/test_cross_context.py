"""Realms of one isolate share handles; another isolate needs a transfer."""

import gc

import pytest

from pyv8c import pyv8


class Window:
    pass


def test_shared_member_callbacks_survive_the_creating_realm():
    class Interface:
        def read(self):
            return 42

        def get_value(self):
            return 17

    spec = pyv8.IdlSpec(
        methods=(pyv8.MethodSpec(name="read", callback="read"),),
        attributes=(pyv8.AttributeSpec(name="value", getter="get_value"),),
    )

    def factory(name):
        if name == "Interface":
            return Interface, {name: spec}, {name: True}
        return None

    isolate = pyv8.Isolate()
    source = isolate.context(Window, lazy_factory=factory)
    target = isolate.context(Window, lazy_factory=factory)
    try:
        source.expose(item=Interface())
        target.expose(item=Interface())
        assert source.exec_js("JSON.stringify([item.read(), item.value])") == "[42,17]"
        source.close()
        assert target.exec_js("JSON.stringify([item.read(), item.value])") == "[42,17]"
    finally:
        source.close()
        target.close()
        isolate.close()


def test_an_idl_host_object_keeps_its_wrapper_across_realms():
    class Interface:
        pass

    spec = pyv8.IdlSpec()

    def factory(name):
        if name == "Interface":
            return Interface, {name: spec}, {name: True}
        return None

    isolate = pyv8.Isolate()
    source = isolate.context(Window, lazy_factory=factory)
    target = isolate.context(Window, lazy_factory=factory)
    try:
        item = Interface()
        source.expose(item=item)
        target.expose(item=item)
        from_target = target.exec_js("item")
        source.expose(other=from_target)
        assert source.exec_js("item === other") is True
    finally:
        source.close()
        target.close()
        isolate.close()


def test_a_shared_child_does_not_share_a_realm_bound_ancestor():
    class Base:
        pass

    class Child(Base):
        pass

    child_spec = pyv8.IdlSpec(parent_class=Base)

    def factory(label):
        base_spec = pyv8.IdlSpec(methods=(pyv8.MethodSpec(
            name="realmName", callback=lambda receiver: label,
        ),))

        def answer(name):
            if name == "Child":
                return (Child, {"Base": base_spec, "Child": child_spec},
                        {"Base": False, "Child": False})
            if name == "Base":
                return Base, {"Base": base_spec}, {"Base": False}
            return None

        return answer

    isolate = pyv8.Isolate()
    first = isolate.context(Window, lazy_factory=factory("first"))
    second = isolate.context(Window, lazy_factory=factory("second"))
    try:
        first.expose(item=Child())
        second.expose(item=Child())
        assert first.exec_js("item.realmName()") == "first"
        assert second.exec_js("item.realmName()") == "second"
    finally:
        first.close()
        second.close()
        isolate.close()


def test_a_handle_from_another_realm_of_the_same_isolate_is_usable():
    isolate = pyv8.Isolate()
    source = isolate.context(Window)
    target = isolate.context(Window)
    try:
        handle = source.exec_js("({ items: [1, 2, 3] })")
        assert target.import_handle(handle) is handle
        target.expose(foreign=handle)
        assert target.exec_js("foreign.items.length") == 3
    finally:
        source.close()
        target.close()
        isolate.close()


def test_closing_the_source_realm_keeps_the_target_usable():
    isolate = pyv8.Isolate()
    source = isolate.context(Window)
    target = isolate.context(Window)
    handle = source.exec_js("({ items: [1, 2, 3] })")
    target.expose(foreign=handle)
    source.close()
    del handle
    gc.collect()
    assert target.exec_js("foreign.items.length") == 3
    target.close()
    assert isolate.close()


def test_transfer_copies_structure_into_another_isolate():
    first = pyv8.Isolate()
    second = pyv8.Isolate()
    source = first.context(Window)
    target = second.context(Window)
    try:
        target.expose(copied=pyv8.transfer(source.exec_js("({ items: [1, 2, 3] })"), target))
        assert target.exec_js("JSON.stringify(copied)") == '{"items":[1,2,3]}'
        target.expose(other=pyv8.transfer(["a", {"b": [1, None]}], target))
        assert target.exec_js("JSON.stringify(other)") == '["a",{"b":[1,null]}]'
        target.expose(plain=pyv8.transfer(7, target))
        assert target.exec_js("plain + 1") == 8
    finally:
        source.close()
        target.close()
        first.close()
        second.close()


@pytest.mark.parametrize(
    "expression",
    ["(x) => x", "Promise.resolve(1)", "new Uint8Array(2)", "Symbol('s')"],
)
def test_transfer_refuses_what_has_no_meaning_in_another_heap(expression):
    first = pyv8.Isolate()
    second = pyv8.Isolate()
    source = first.context(Window)
    target = second.context(Window)
    try:
        with pytest.raises(TypeError, match="cannot cross isolates"):
            pyv8.transfer(source.exec_js(expression), target)
    finally:
        source.close()
        target.close()
        first.close()
        second.close()


def test_a_handle_cannot_cross_isolates_implicitly():
    first = pyv8.Isolate()
    second = pyv8.Isolate()
    source = first.context(Window)
    target = second.context(Window)
    try:
        with pytest.raises(RuntimeError, match="another isolate"):
            target.import_handle(source.exec_js("({})"))
    finally:
        source.close()
        target.close()
        first.close()
        second.close()
