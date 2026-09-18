"""Stage 3 acceptance: a realm is the unit that owns things, and closing it is
the only thing that has to be correct.

Two properties, both of which the previous binding failed.  Building and closing
a realm a thousand times must leave nothing behind -- no handle, no Python
reference, no wrapper record.  And a host object handed to a realm must be
released by that realm's close, because the realm is the only thing holding it:
if the realm could be dropped while its objects were still referenced, every
page that ever ran would pin its entire object graph for the life of the process.
"""

from __future__ import annotations

import gc
import weakref

import v8n


def test_a_thousand_realms_leave_nothing_behind():
    # Objects rather than bytes: a wrapper record that outlives its realm shows up
    # as a Python object nobody can reach, and counting them is deterministic
    # where a memory reading is not.
    gc.collect()
    baseline = len(gc.get_objects())

    for _ in range(1000):
        realm = v8n.Realm()
        assert realm.evaluate("({a: 1})").a == 1
        realm.close()
        del realm

    gc.collect()
    growth = len(gc.get_objects()) - baseline
    # A thousand realms that each leaked one object would show a thousand.  The
    # allowance is for interpreter noise (interned strings, caches), not for a
    # per-realm cost.
    assert growth < 200, f"1000 realms left {growth} Python objects behind"


def test_a_host_object_is_released_by_the_close_that_owns_it():
    class Host:
        pass

    realm = v8n.Realm()
    identity = realm.evaluate("(o => o)")

    hosts = [Host() for _ in range(100)]
    for host in hosts:
        assert identity(host) is host

    references = [weakref.ref(host) for host in hosts]
    del hosts, host
    gc.collect()
    # The realm's wrapper records are the only thing keeping them alive, which is
    # what makes an object reachable from JavaScript at all.
    assert sum(1 for reference in references if reference() is not None) == 100

    realm.close()
    gc.collect()
    assert sum(1 for reference in references if reference() is not None) == 0


def test_handles_do_not_outlive_the_realm_that_made_them():
    # A handle holds a Python reference to its realm, so a realm cannot be
    # destroyed while one of its handles is still reachable -- and once the realm
    # closes, the handle is a tombstone rather than a dangling pointer.
    realm = v8n.Realm()
    handles = [realm.evaluate(f"[{index}, {index + 1}]") for index in range(50)]
    assert handles[0].to_python() == [0, 1]

    realm.close()
    for handle in handles:
        try:
            handle.to_python()
        except RuntimeError:
            continue
        raise AssertionError("a handle outlived its realm")


def test_every_realm_on_one_isolate_closes_independently():
    # pyv8c runs a page and its workers on separate isolates, but nothing stops a
    # host from putting several realms on one.  Each still owns its own context
    # and its own records, so closing one must not disturb the other.
    isolate = v8n.Isolate()
    first = v8n.Realm(isolate=isolate)
    second = v8n.Realm(isolate=isolate)
    try:
        first.evaluate("globalThis.tag = 'first'")
        second.evaluate("globalThis.tag = 'second'")
        assert first.evaluate("globalThis.tag") == "first"
        assert second.evaluate("globalThis.tag") == "second"

        first.close()
        assert first.closed is True
        assert second.closed is False
        assert second.evaluate("globalThis.tag") == "second"
    finally:
        second.close()
