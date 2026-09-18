#include "boundary/guard.h"
#include "kernel/v8_identity.h"
#include "value/value_internal.h"
#include "v8n/realm.h"
#include "v8n/value.h"

namespace v8n
{

bool
microtasks_available()
{
    // v8::MicrotaskQueue's layout follows V8_CPPGC_MICROTASK_QUEUE, which V8
    // passes on its own compile command line and ships in no public header.
    // Built without it, this translation unit and the library disagree about the
    // class, and every call into the queue -- including the one that asks the
    // context for it -- goes through the wrong vtable slot.  Refusing is the only
    // honest answer: a queue that faults on first use is worse than one that was
    // never offered.
    if (!microtask_queue_supported())
        return false;
    return true;
}

bool
Realm::enqueue_microtask(PyObject* callable)
{
    if (!require_active("enqueue_microtask"))
        return false;
    if (callable == nullptr || !PyCallable_Check(callable))
    {
        raise(PyExc_TypeError, "enqueue_microtask() takes a callable");
        return false;
    }
    if (!microtasks_enabled_)
    {
        raise(PyExc_RuntimeError,
              "this realm has no microtask queue; queueMicrotask and promise reactions "
              "cannot run in it");
        return false;
    }

    IsolateEntry entry(*isolate_);
    if (!entry.ok())
        return false;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> local = context();
    v8::Context::Scope context_scope(local);

    // The queue belongs to the context.  Asking for it instead of remembering a
    // pointer also means the answer is the one V8 actually installed, rather than
    // one this code believed it installed.
    v8::MicrotaskQueue* queue = local->GetMicrotaskQueue();
    if (queue == nullptr)
    {
        raise(PyExc_RuntimeError, "the realm's context has no microtask queue");
        return false;
    }

    // The callable crosses as a host callable, so the microtask holds a reference
    // to it through the same ownership machinery as every other crossing: the
    // reference goes when the microtask does, or when the realm closes.
    v8::Local<v8::Value> function = to_js(callable, *this);
    if (function.IsEmpty())
        return false;
    if (!function->IsFunction())
    {
        raise(PyExc_TypeError, "enqueue_microtask() takes a callable");
        return false;
    }

    queue->EnqueueMicrotask(isolate, function.As<v8::Function>());
    return true;
}

bool
Realm::run_microtasks()
{
    if (state_ == State::kClosed)
        return true;
    if (!microtasks_enabled_)
        return true;

    IsolateEntry entry(*isolate_);
    if (!entry.ok())
        return false;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> local = context();
    if (local.IsEmpty())
        return true;

    v8::MicrotaskQueue* queue = local->GetMicrotaskQueue();
    if (queue == nullptr)
        return true;

    // No context scope: each microtask carries the context it was created in, and
    // V8 restores it.  Entering this realm's context here would run another
    // realm's microtask in the wrong global.
    queue->PerformCheckpoint(isolate);

    // A checkpoint can drop the last reference to a host object, so the records
    // whose JavaScript side died are finished off here rather than at the next
    // arbitrary entry.
    reap_owners();
    return true;
}

}  // namespace v8n
