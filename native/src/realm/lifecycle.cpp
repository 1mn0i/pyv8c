// The realm's state machine.
//
// Closing is a sequence, not a flag.  Entry has to be refused first, so nothing
// new starts; the resources go in the one order that keeps each step's Python
// callbacks from seeing a half dismantled realm; and the context goes last.
// Because the sequence can be interrupted -- the only way it can fail is that the
// caller is not the isolate's owner thread -- close() is idempotent and reports
// what it managed to do, so the owner thread can call it again and finish.

#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "realm/realm_internal.h"
#include "realm/resources.h"
#include "v8n/kernel.h"
#include "v8n/realm.h"
#include "v8n/value.h"

namespace v8n
{

std::size_t
Realm::release_owners()
{
    std::size_t released = 0;

    // Detached in one step, then released one by one: releasing drops a Python
    // reference, and a finalizer that re-enters the realm must not find the list
    // it is walking.
    PythonOwner* record = owners_;
    owners_ = nullptr;
    while (record != nullptr)
    {
        PythonOwner* next = record->next;
        record->next = nullptr;
        record->previous = nullptr;
        record->realm = nullptr;
        release_python_owner(*record);
        ++released;
        record = next;
    }
    return released;
}

std::size_t
Realm::discard_owners_without_isolate()
{
    std::size_t discarded = 0;

    PythonOwner* record = owners_;
    owners_ = nullptr;
    while (record != nullptr)
    {
        PythonOwner* next = record->next;
        discard_python_owner(*record);
        ++discarded;
        record = next;
    }
    return discarded;
}

bool
Realm::close()
{
    return close(nullptr);
}

bool
Realm::close(CloseReport* report)
{
    CloseReport local;
    CloseReport& outcome = report == nullptr ? local : *report;

    if (state_ == State::kClosed)
    {
        outcome.closed = true;
        return true;
    }
    // Entering kClosing is what refuses new work.  It happens before anything is
    // released, so a callback that was already in flight when close() started
    // cannot start something new underneath it.
    state_ = State::kClosing;

    // Every step below needs the isolate.  Releasing a retained handle needs the
    // isolate that owns it, and releasing a wrapper record can run a Python
    // finalizer that reaches back into the context.  A thread that cannot enter
    // the isolate therefore cannot close a realm, and saying so beats doing half
    // the job and calling it done.
    IsolateEntry entry(*isolate_);
    if (!entry.ok())
    {
        // The error stays set: refusing is a real answer, and the caller needs
        // to know why the realm is now closing but not closed.  A destructor is
        // the one caller that clears it, because there is no frame to raise in.
        outcome.closed = false;
        outcome.retryable = true;
        outcome.blocked_by = "the isolate cannot be entered from this thread";
        return false;
    }

    v8::HandleScope handle_scope(entry.v8());

    // The watchdog goes first: it terminates the isolate by pointer, and it must
    // not do that while the isolate is being taken apart.
    if (timeout_guard_ != nullptr)
        timeout_guard_->disarm();

    // A layer's own state goes before the roots it was using: the bridge's batch
    // is reachable from the root set, so releasing the batch first would leave a
    // root pointing at freed storage.
    // Taken out in one step and released one by one, so a state whose release
    // re-enters the realm cannot find the list it is walking.
    std::vector<OwnedState> states;
    states.swap(owned_states_);
    for (const OwnedState& entry_state : states)
    {
        if (entry_state.state != nullptr && entry_state.release != nullptr)
            entry_state.release(entry_state.state);
    }

    // Wrapper records, with the context still alive.  Each drops a Python
    // reference, and a finalizer that runs then must not find a realm whose
    // context is already gone.
    outcome.owners_released = release_owners();

    // Then the realm's own roots: the error hooks and the trace hook.  Same
    // reason, same order.  Hooks installed during teardown land on the rebuilt
    // registry and are released by the next close().
    if (registry_ != nullptr)
        outcome.roots_released = registry_->clear();

    if (fallback_template_ != nullptr)
    {
        fallback_template_->reset();
        delete fallback_template_;
        fallback_template_ = nullptr;
    }

    // The per-type templates go with it.  The type pointers are dropped without
    // touching Python: a realm that is closing must not run a finalizer for the
    // classes it was installed against.
    for (RegisteredType& entry : registered_types_)
    {
        entry.template_slot->reset();
        delete entry.template_slot;
        entry.template_slot = nullptr;
        entry.type = nullptr;
    }
    registered_types_.clear();
    unregistered_types_.clear();

    // The installed-interface templates go with them, for the same reason: they
    // are retained handles, and the isolate is the only thing that may release
    // them.  The names go too -- a realm that is closed has installed nothing.
    for (InstalledInterface& entry : installed_interfaces_)
    {
        entry.template_slot->reset();
        delete entry.template_slot;
        entry.template_slot = nullptr;
    }
    installed_interfaces_.clear();

    // The microtask queue belongs to the context, so releasing the context
    // releases it.  Nothing here has to order the two, which is the point of not
    // owning it.
    context_.Reset();

    terminated_ = false;
    state_ = State::kClosed;
    outcome.closed = true;
    return true;
}

void
Realm::destroy(Realm* realm)
{
    delete realm;
}

Realm::~Realm()
{
    // Whatever error state was already pending survives: for a caller of a failed
    // Realm::create it is the only thing to go on, and clearing it here is what
    // turns "your description is wrong" into "returned NULL without setting an
    // exception".  What close() sets is a different matter, and the restore below
    // is what drops that.
    PyObject* error_type = nullptr;
    PyObject* error_value = nullptr;
    PyObject* error_traceback = nullptr;
    PyErr_Fetch(&error_type, &error_value, &error_traceback);

    CloseReport report;
    if (!close(&report) && report.retryable)
    {
        // Destroyed from a thread that cannot enter the isolate, which happens
        // when a Python reference count reaches zero on a worker thread.  The
        // Python references still have to go, or the host objects they hold leak
        // for the life of the process.
        //
        // The retained V8 handles must not be touched from here: releasing a
        // Global requires the isolate, and the isolate belongs to another
        // thread.  They are leaked on purpose -- the handle storage dies with
        // the isolate, and the alternative is undefined behaviour.
        discard_owners_without_isolate();
        if (registry_ != nullptr)
            registry_->clear();
    }
    // Back to what was there on entry, which for a deallocation is nothing and for
    // a failed create is the error that explains the failure.  Either way the
    // destructor leaves no exception of its own behind.
    PyErr_Restore(error_type, error_value, error_traceback);
}

}  // namespace v8n
