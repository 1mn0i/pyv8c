#include "v8n/boundary.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "v8n/realm.h"

namespace v8n
{

PyEntry::PyEntry(Realm& realm)
{
    // A closing realm refuses entry before anything else happens, so a call
    // that was already in flight when close() started cannot re-enter.
    if (!realm.require_active("entering the realm"))
        return;

    auto entry = std::make_unique<IsolateEntry>(realm.isolate());
    if (!entry->ok())
        return;

    // Open a handle scope before the first Local exists: realm.context()
    // produces one, and there is nowhere for it to live until this is open.
    handle_scope_.emplace(entry->v8());

    v8::Local<v8::Context> context = realm.context();
    if (context.IsEmpty())
    {
        raise(PyExc_RuntimeError, "the realm has no context");
        return;
    }

    context_scope_ = std::make_unique<v8::Context::Scope>(context);
    realm_ = &realm;
    // Named for the whole crossing: the microtask checkpoint that V8 runs when
    // this entry unwinds is where a microtask that threw is reported.
    report_scope_.emplace(&realm);
    entry_ = std::move(entry);
}

v8::Local<v8::Context>
PyEntry::context()
{
    return realm_ == nullptr ? v8::Local<v8::Context>() : realm_->context();
}

PyCallback::PyCallback(const char* what) : what_(what)
{
    // Capture the caller's error state so the callback starts clean.  Anything
    // the callback leaves behind is reported below instead of surfacing at the
    // next unrelated Python call.
    PyErr_Fetch(&error_type_, &error_value_, &error_traceback_);
}

PyCallback::~PyCallback()
{
    PyErr_Restore(error_type_, error_value_, error_traceback_);
}

void
PyCallback::report(PyObject* subject)
{
    failed_ = true;
    if (subject != nullptr)
        PyErr_WriteUnraisable(subject);
    else
        PyErr_Clear();
}

PyObject*
PyCallback::call(PyObject* callable, PyObject* arguments)
{
    if (callable == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: no callable", what_);
        report(callable);
        return nullptr;
    }

    PyObject* result = PyObject_CallObject(callable, arguments);
    if (result == nullptr)
        report(callable);
    return result;
}

PyObject*
PyCallback::call_method(PyObject* receiver, const char* name)
{
    if (receiver == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: no receiver", what_);
        report(receiver);
        return nullptr;
    }

    PyObject* result = PyObject_CallMethod(receiver, name, nullptr);
    if (result == nullptr)
        report(receiver);
    return result;
}

}  // namespace v8n
