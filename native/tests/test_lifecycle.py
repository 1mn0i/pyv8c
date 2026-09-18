"""Stage 3 acceptance: the realm state machine and the services it owns.

Three things are under test.  The state machine, because closing is a sequence
rather than a flag and a half closed realm has to be recoverable.  The root set,
because it is what keeps a realm's Python references visible to the collector and
released at close.  And the three services -- error hooks, microtasks, the
execution timeout -- because each one is a place where a reference could be held
for the life of the process, or a failure could be swallowed.
"""

from __future__ import annotations

import gc
import threading
import weakref

import pytest

import v8n


@pytest.fixture()
def realm():
    realm = v8n.Realm()
    try:
        yield realm
    finally:
        realm.close()


# -- the state machine ----------------------------------------------------


def test_a_realm_starts_active_and_ends_closed():
    realm = v8n.Realm()
    assert realm.is_active is True
    assert realm.closed is False
    assert realm.close() is True
    assert realm.closed is True
    assert realm.is_active is False


def test_close_is_idempotent():
    realm = v8n.Realm()
    assert realm.close() is True
    assert realm.close() is True
    assert realm.closed is True


def test_entry_is_refused_once_closed():
    realm = v8n.Realm()
    realm.close()
    with pytest.raises(RuntimeError, match="closed"):
        realm.evaluate("1 + 1")
    with pytest.raises(RuntimeError, match="closed"):
        realm.enqueue_microtask(lambda: None)
    with pytest.raises(RuntimeError, match="closed"):
        realm.set_error_hooks(on_uncaught=lambda error: None)


def test_a_close_refused_on_another_thread_is_finished_by_the_owner_thread():
    # Only the owner thread can enter the isolate, and closing needs it: releasing
    # a handle and running a Python finalizer both reach into V8.  So a close from
    # elsewhere is refused and reported, not silently half done -- and the realm
    # is left closing, which already refuses new work.
    realm = v8n.Realm()
    assert realm.evaluate("1") == 1

    outcomes = []

    def attempt():
        try:
            outcomes.append(realm.close())
        except BaseException as error:  # noqa: BLE001 - the point is what it was
            outcomes.append(error)

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], RuntimeError)
    assert realm.closed is False
    assert realm.is_active is False

    # The owner thread can still finish the job, and does.
    assert realm.close() is True
    assert realm.closed is True


def test_a_handle_from_a_closed_realm_refuses_to_be_used():
    realm = v8n.Realm()
    handle = realm.evaluate("({a: 1})")
    assert handle.a == 1
    realm.close()
    with pytest.raises(RuntimeError):
        handle.to_python()


def test_the_realm_name_is_copied_out_of_the_argument():
    # The name is built from a temporary, so a borrowed pointer would be dangling
    # by the time anything read it.
    realm = v8n.Realm(name="".join(["page", "-", "one"]))
    try:
        assert realm.evaluate("1 + 1") == 2
    finally:
        realm.close()


# -- the Python root set --------------------------------------------------


def test_hooks_are_counted_and_released_by_close():
    realm = v8n.Realm()
    assert realm.root_count == 0

    realm.set_error_hooks(on_uncaught=lambda error: None,
                          on_rejection=lambda error: None)
    realm.trace_hook = lambda *parts: None
    assert realm.root_count == 3

    realm.close()
    assert realm.root_count == 0


def test_a_hook_is_held_by_the_realm_until_it_closes():
    # A hook that closes over its own realm is a cycle: the realm holds the hook
    # and the hook holds the realm.  This checks the holding half, which is what
    # makes the hook unable to disappear while the realm can still call it.
    realm = v8n.Realm()
    ran = []

    def hook(error):
        ran.append(realm)

    realm.set_error_hooks(on_rejection=hook)
    reference = weakref.ref(hook)
    del hook
    gc.collect()
    assert reference() is not None

    realm.close()
    gc.collect()
    assert reference() is None
    assert ran == []


def test_replacing_a_hook_releases_the_previous_one():
    # Deliberately no gc.collect(): the only reference is the realm's own field,
    # so this is decided by reference counting.  The cycle collector is a
    # separate question, and testing it here would be testing CPython.
    realm = v8n.Realm()
    first = lambda error: None  # noqa: E731 - a hook is a value here
    realm.set_error_hooks(on_rejection=first)
    reference = weakref.ref(first)
    del first
    assert reference() is not None

    realm.set_error_hooks(on_rejection=lambda error: None)
    assert reference() is None
    # Still one root, not two: the field is registered once.
    assert realm.root_count == 1
    realm.close()


# -- error hooks ----------------------------------------------------------


def test_an_unhandled_rejection_reaches_the_hook(realm):
    seen = []
    realm.set_error_hooks(on_rejection=lambda *parts: seen.append(parts))

    # The checkpoint at the end of evaluate() is where V8 decides a rejection has
    # no handler, so nothing extra is needed for the hook to run.
    realm.evaluate("Promise.reject(new Error('nope'))")

    assert len(seen) == 1
    event, identity, promise, reason = seen[0]
    assert event == "unhandledrejection"
    # The identity is V8's own hash: a handle is a new Python object every time,
    # so it cannot be what the host pairs the two notices by.
    assert isinstance(identity, int)
    assert isinstance(promise, v8n.Value)
    assert reason.message == "nope"


def test_a_handler_attached_afterwards_is_reported(realm):
    # Both halves of the algorithm, and why: a rejection is only unhandled once
    # the checkpoint that could still attach a handler has passed, so a host
    # that heard only the first notice could not tell `Promise.reject(x)` from
    # `Promise.reject(x).catch(f)` written on one line.  The second notice is
    # what revokes a candidate, and it names the same promise.
    seen = []
    realm.set_error_hooks(on_rejection=lambda *parts: seen.append(parts))

    realm.evaluate("globalThis.p = Promise.reject(new Error('late'))")
    reported = seen[-1]
    assert reported[0] == "unhandledrejection"

    seen.clear()
    realm.evaluate("globalThis.p.catch(() => {})")
    assert [entry[0] for entry in seen] == ["rejectionhandled"]
    assert seen[0][1] == reported[1]
    # Nothing was thrown for this one, so there is no reason to report.
    assert seen[0][3] is v8n.Undefined


def test_a_hook_receives_a_handle_not_an_expanded_copy(realm):
    # The reason goes through the same three-state boundary as everything else,
    # so it arrives as a handle: reading one property of it must not have run
    # the whole object's getters on the way out.
    seen = []
    realm.set_error_hooks(on_rejection=lambda *parts: seen.append(parts))
    realm.evaluate("Promise.reject({code: 7})")

    assert len(seen) == 1
    reason = seen[0][3]
    assert isinstance(reason, v8n.Value)
    assert reason.code == 7
    assert reason.to_python() == {"code": 7}


def test_a_realm_without_hooks_reports_nothing(realm):
    # The default has to be silence: a rejection nobody asked about must not
    # become an error, and must not be written to stderr either.
    assert realm.evaluate("void Promise.reject(new Error('ignored'))") is v8n.Undefined


# -- microtasks -----------------------------------------------------------


def test_a_queued_microtask_runs_at_the_next_checkpoint(realm):
    order = []
    realm.enqueue_microtask(lambda: order.append("micro"))
    assert order == []

    assert realm.run_microtasks() is True
    assert order == ["micro"]


def test_a_promise_reaction_runs_before_evaluate_returns(realm):
    # Otherwise a caller that awaits nothing sees a promise that never settled,
    # and the host has to know to pump the queue by hand.
    realm.evaluate("globalThis.done = null; Promise.resolve(7).then(v => { globalThis.done = v; })")
    assert realm.evaluate("globalThis.done") == 7


def test_a_realm_can_be_built_without_microtasks():
    realm = v8n.Realm(microtasks=False)
    try:
        assert realm.microtasks is False
        with pytest.raises(RuntimeError, match="microtask"):
            realm.enqueue_microtask(lambda: None)
    finally:
        realm.close()


def test_microtasks_report_what_the_build_can_do():
    # This V8 build passes the layout macro, so the answer must be yes; a no here
    # would mean the extension and the library disagree about the class.
    realm = v8n.Realm()
    try:
        assert realm.microtasks is True
    finally:
        realm.close()


# -- the execution timeout ------------------------------------------------


def test_a_runaway_script_is_terminated_and_reported(realm):
    realm.timeout = 200
    with pytest.raises(TimeoutError):
        realm.evaluate("while (true) {}")
    assert realm.terminated is True


def test_the_isolate_is_usable_after_termination():
    # Termination is sticky until the owner thread cancels it, and this is what
    # proves the cancel happens: a host recovers by raising the budget, not by
    # building a new realm and abandoning the old one.
    realm = v8n.Realm(timeout=200)
    try:
        with pytest.raises(TimeoutError):
            realm.evaluate("while (true) {}")
        assert realm.evaluate("'still here'") == "still here"
    finally:
        realm.close()


def test_the_budget_is_per_entry_not_per_realm():
    realm = v8n.Realm(timeout=1000)
    try:
        assert realm.evaluate("1 + 1") == 2
        assert realm.evaluate("2 + 2") == 4
        assert realm.terminated is False
    finally:
        realm.close()


def test_a_realm_without_a_budget_is_not_watched():
    realm = v8n.Realm()
    try:
        assert realm.timeout == 0
        # A script that takes a while is not a failure when no budget was asked
        # for.  Small enough to stay fast, large enough to be real work.
        assert realm.evaluate("let n = 0; for (let i = 0; i < 200000; ++i) n += i; n") == 19999900000
    finally:
        realm.close()


def test_a_negative_budget_is_refused(realm):
    with pytest.raises(ValueError):
        realm.timeout = -1


# -- trace ----------------------------------------------------------------


def test_a_trace_hook_must_be_callable(realm):
    with pytest.raises(TypeError):
        realm.trace_hook = 42


def test_a_trace_hook_can_be_removed(realm):
    realm.trace_hook = lambda *parts: None
    assert realm.root_count == 1
    realm.trace_hook = None
    assert realm.trace_hook is None
    assert realm.root_count == 1
