#ifndef V8N_BOUNDARY_H
#define V8N_BOUNDARY_H

// The two guards that every crossing goes through.
//
// PyEntry  -- a Python call entering V8: checks the realm is open, checks the
//             thread owns the isolate, and enters the context.
// PyCallback -- V8 calling back into Python: keeps the caller's error state
//             from leaking into the callback, and turns a failing callback into
//             a reported unraisable instead of an error that would surface at
//             the next unrelated Python call.
//
// Having exactly two is the point.  The previous binding performed the same
// fetch/call/restore dance inline in a dozen places, and called Py_DECREF from
// inside V8 weak callbacks where it is not safe to raise.

#include "v8n/realm.h"
#include "v8n/v8.h"

#include <memory>
#include <optional>

namespace v8n
{

// A Python call entering a realm.
//
// Entering is three things, and all of them are required before the first
// v8::Local may be created: the isolate has to become this thread's current
// isolate, a handle scope has to be open so locals have somewhere to live, and
// the realm's context has to become the current context.  Skipping the context
// leaves GetCurrentContext() empty -- which V8 tolerates only for the fastest
// property reads -- and skipping the handle scope aborts inside
// HandleScope::CreateHandle.  Keeping all three in the guard is what stops a
// call site from getting one and forgetting the others.
class PyEntry
{
public:
    explicit PyEntry(Realm& realm);

    PyEntry(const PyEntry&) = delete;
    PyEntry& operator=(const PyEntry&) = delete;

    // False when the realm is closing or the caller does not own the isolate;
    // the Python error is already set.
    bool ok() const { return context_scope_ != nullptr; }
    v8::Isolate* v8() const { return entry_ == nullptr ? nullptr : entry_->v8(); }
    v8::Local<v8::Context> context();

private:
    // All three are conditional, because a refused entry has to leave the
    // object constructible and inspectable.  Declared innermost first: members
    // are destroyed in reverse, and the context scope has to go before the
    // handle scope it allocated in.
    //
    // v8::HandleScope deletes its own operator new to force stack allocation,
    // so it cannot live behind a unique_ptr and is held in an optional instead.
    std::unique_ptr<IsolateEntry> entry_;
    std::optional<v8::HandleScope> handle_scope_;
    std::unique_ptr<v8::Context::Scope> context_scope_;
    Realm* realm_ = nullptr;
};

// A Python call made from inside V8.
//
// The pending Python error state is captured on construction, so the callback
// starts from a clean slate; it is restored on destruction, so nothing the
// callback did can surface at a later, unrelated call.  A callback that fails
// is written as unraisable, because there is no Python frame to raise into.
class PyCallback
{
public:
    // what names the callback in the unraisable report.
    explicit PyCallback(const char* what);
    ~PyCallback();

    PyCallback(const PyCallback&) = delete;
    PyCallback& operator=(const PyCallback&) = delete;

    // Call callable with the positional arguments in tuple (may be null).
    // Returns a new reference, or nullptr when the call failed, in which case
    // the failure has been reported and failed() is true.
    PyObject* call(PyObject* callable, PyObject* arguments);

    // Call a no-argument method by name.
    PyObject* call_method(PyObject* receiver, const char* name);

    bool failed() const { return failed_; }

private:
    void report(PyObject* subject);

    const char* what_;
    PyObject* error_type_ = nullptr;
    PyObject* error_value_ = nullptr;
    PyObject* error_traceback_ = nullptr;
    bool failed_ = false;
};

// Translate a pending Python error into a JavaScript value.
//
// Returns a value to throw, or an empty handle when the error could not be
// translated and a Python error is set instead.  The mapping is a single table:
// a Python exception names its JavaScript counterpart through
// __v8_error_name__, and a host may register the reverse direction with
// set_error_map().
v8::Local<v8::Value> python_error_to_js(v8::Isolate* isolate);

// Translate a pending Python error and throw it.
//
// The common case, and the only one a callback should use.  Handing back the
// value to throw leaves the throw itself to the caller, and a caller that
// forgets produces the worst possible outcome: the JavaScript call succeeds and
// returns undefined, so nothing downstream can tell a failure from a result.
void throw_python_error(v8::Isolate* isolate);

// Raise the JavaScript value as a Python exception.
void js_exception_to_python(v8::Local<v8::Value> exception, Realm& realm);

// Register the JavaScript error name -> Python exception class mapping.
//
// The values may be exception classes or callables taking (name, message).
// Kept host owned because pyv8c defines its own error hierarchy; hard coding a
// list here would mean two places to change whenever an interface adds an error
// name.
bool set_error_map(PyObject* mapping);

// The currently registered mapping, borrowed.
PyObject* error_map();

}  // namespace v8n

#endif
