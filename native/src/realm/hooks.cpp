#include "boundary/guard.h"
#include "realm/realm_internal.h"
#include "value/value_internal.h"
#include "v8n/boundary.h"
#include "v8n/realm.h"
#include "v8n/value.h"

namespace v8n
{
namespace
{

// One callback per isolate, dispatching on the promise's realm.
//
// V8 offers no per-context rejection hook, so the callback has to look up the
// realm itself.  A promise created in a realm that has no hook installed is
// dropped rather than reported to whichever realm happens to be current:
// reporting another page's rejection to this page's hook is worse than saying
// nothing, because the host cannot tell the difference.
//
// Both rejection events are forwarded, because the host cannot implement the
// specification's algorithm from one of them: a rejection is only unhandled
// once the checkpoint that could still attach a handler has passed, and a
// handler attached before then is exactly what the second event announces.
void promise_reject_callback(v8::PromiseRejectMessage message)
{
    const v8::PromiseRejectEvent event = message.GetEvent();
    if (event != v8::kPromiseRejectWithNoHandler
        && event != v8::kPromiseHandlerAddedAfterReject)
        return;

    v8::Local<v8::Promise> promise = message.GetPromise();
    if (promise.IsEmpty())
        return;

    Realm* realm = Realm::from_context(promise->GetCreationContextChecked());
    if (realm == nullptr)
        return;

    // The value is empty on the "handler added" event: nothing was thrown, so
    // there is no reason to report and the promise is the whole story.
    realm->report_rejection(promise, message.GetValue(),
                            event == v8::kPromiseRejectWithNoHandler);
}

}  // namespace

void
hooks_install(Isolate& isolate)
{
    // Installed unconditionally, and that is deliberate.  Remembering which
    // isolates already have it would mean remembering an address, and V8 reuses
    // an isolate's address once the isolate is destroyed: a new isolate in the
    // same slot would look like one that was already set up and would silently
    // never receive a rejection.  Setting the callback is idempotent, so there is
    // nothing to save by caching.
    isolate.v8()->SetPromiseRejectCallback(promise_reject_callback);
}

bool
Realm::set_error_hooks(PyObject* on_uncaught, PyObject* on_rejection)
{
    // A closed realm has already released its root set, so a hook installed now
    // would be held by nothing and never released.  Refusing is the only answer
    // that cannot leak.
    if (!require_active("set_error_hooks"))
        return false;

    if (on_uncaught != nullptr && !PyCallable_Check(on_uncaught))
    {
        raise(PyExc_TypeError, "the uncaught error hook must be callable");
        return false;
    }
    if (on_rejection != nullptr && !PyCallable_Check(on_rejection))
    {
        raise(PyExc_TypeError, "the rejection hook must be callable");
        return false;
    }

    // Registered before being stored, so a hook installed and then abandoned by
    // a closing realm is still released by the root set.
    if (on_uncaught != nullptr && !register_root(&uncaught_hook_))
        return false;
    if (on_rejection != nullptr && !register_root(&rejection_hook_))
        return false;

    PyObject* previous_uncaught = uncaught_hook_;
    uncaught_hook_ = on_uncaught;
    Py_XINCREF(uncaught_hook_);
    Py_XDECREF(previous_uncaught);

    PyObject* previous_rejection = rejection_hook_;
    rejection_hook_ = on_rejection;
    Py_XINCREF(rejection_hook_);
    Py_XDECREF(previous_rejection);

    // Installed on first use: a realm that never asks for rejection reporting
    // costs V8 nothing.
    if (on_rejection != nullptr)
        hooks_install(*isolate_);
    return true;
}

void
Realm::report_rejection(v8::Local<v8::Promise> promise, v8::Local<v8::Value> reason,
                        bool unhandled)
{
    PyObject* hook = rejection_hook_;
    if (hook == nullptr)
        return;

    PyCallback guard("an unhandled rejection hook");

    // The identity is V8's own hash for the promise, which is what lets the
    // host pair the two notices without comparing handles: a handle is a new
    // Python object every time, so it cannot be the key.
    PyRef event(PyRef::adopt(PyUnicode_FromString(
        unhandled ? "unhandledrejection" : "rejectionhandled")));
    PyRef identity(PyRef::adopt(PyLong_FromLong(promise->GetIdentityHash())));
    PyRef transferred(PyRef::adopt(
        value_wrapper(*this, promise)));
    PyRef value(PyRef::adopt(reason.IsEmpty()
                                 ? PyRef::borrow(value::undefined_singleton()).release()
                                 : to_python_boundary(reason, *this)));
    if (!event || !identity || !transferred || !value)
    {
        PyErr_WriteUnraisable(hook);
        return;
    }

    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(
        hook, event.get(), identity.get(), transferred.get(), value.get(), nullptr)));
    if (!result)
    {
        // There is no Python frame to raise into, and there is no JavaScript
        // frame either: this is the report of an error that already happened.
        PyErr_WriteUnraisable(hook);
    }
}

void
Realm::report_uncaught(v8::Local<v8::Value> exception)
{
    PyObject* hook = uncaught_hook_;
    if (hook == nullptr)
        return;

    PyCallback guard("an uncaught error hook");
    PyRef argument(PyRef::adopt(to_python_boundary(exception, *this)));
    if (!argument)
    {
        PyErr_WriteUnraisable(hook);
        return;
    }

    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(hook, argument.get(), nullptr)));
    if (!result)
        PyErr_WriteUnraisable(hook);
}

}  // namespace v8n
