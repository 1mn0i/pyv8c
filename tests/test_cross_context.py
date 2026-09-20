"""Realms of one isolate share handles; another isolate needs a transfer."""

import gc

import pytest

import pyv8


class Window:
    pass


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
