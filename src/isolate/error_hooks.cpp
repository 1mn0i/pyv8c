#include "error_hooks.h"

// pyv8.h first: it carries the MSVC shims V8's public headers expect (see the
// NO_RETURN handling in isolate_state.h), so it has to precede <v8.h>.
#include "pyv8.h"

#include "convert.h"
#include "context.h"
#include "isolate_scope.h"
#include "isolate_state.h"

using namespace v8;

namespace
{

// The realm whose isolate is running right now: both hooks fire from inside a
// realm thread executing JavaScript, so no other thread can reach them.
IsolateState*
reporting_isolate()
{
    IsolateState* state = current_isolate_state();
    if (state == nullptr || !state->is_active())
        return nullptr;
    return state;
}

// A Python reference to *value*, or None: empty handles are normal here, since a
// rejection notification carries no value for some of its events.
PyObject*
to_python(Local<Value> value, Local<Context> context)
{
    if (value.IsEmpty())
    {
        Py_INCREF((PyObject*)Py_None);
        return Py_None;
    }
    return py_from_js(value, context);
}

// Calls *callback*, saving and restoring the Python error state around it: the
// Python reached here is arbitrary page code, and a reporter that fails is
// reported as unraisable rather than replacing the JavaScript error it was called
// about.
void
call_reporter(PyObject* callback, PyObject* arguments)
{
    PyObject* error_type = nullptr;
    PyObject* error_value = nullptr;
    PyObject* error_traceback = nullptr;
    PyErr_Fetch(&error_type, &error_value, &error_traceback);
    PyObject* result = PyObject_CallObject(callback, arguments);
    if (result == nullptr)
        PyErr_WriteUnraisable(callback);
    else
        Py_DECREF((PyObject*)result);
    PyErr_Restore(error_type, error_value, error_traceback);
}

void
report_uncaught(IsolateState* state, Local<Message> message, Local<Value> data)
{
    Isolate* isolate = state->isolate();
    HandleScope handle_scope(isolate);
    Local<Context> context = isolate->GetCurrentContext();
    if (context.IsEmpty())
        return;
    auto* owner = context_from_v8(context);
    if (!context_is_active(owner) || owner->uncaught_handler == nullptr) return;
    PyObject* handler = owner->uncaught_handler;
    Py_INCREF((PyObject*)handler);
    PyOwnedRef keep_handler(handler);

    int line = 0;
    int column = 0;
    message->GetLineNumber(context).To(&line);
    message->GetStartColumn(context).To(&column);

    // The exception value arrives as ``data`` because the listener is installed
    // without one of its own: V8 hands over the thrown value in that case.
    PyObject* error = nullptr;
    if (data.IsEmpty() || data->IsUndefined())
    {
        error = Py_None;
        Py_INCREF((PyObject*)error);
    }
    else
    {
        error = py_from_js(data, context);
    }
    PyObject* text = to_python(message->Get(), context);
    PyObject* source = to_python(message->GetScriptResourceName(), context);
    PyObject* py_line = PyLong_FromLong(line);
    PyObject* py_column = PyLong_FromLong(column);
    PyObject* arguments = (text != nullptr && source != nullptr && py_line != nullptr &&
                           py_column != nullptr && error != nullptr)
        ? PyTuple_Pack(5, text, source, py_line, py_column, error)
        : nullptr;
    Py_XDECREF((PyObject*)text);
    Py_XDECREF((PyObject*)source);
    Py_XDECREF((PyObject*)py_line);
    Py_XDECREF((PyObject*)py_column);
    Py_XDECREF((PyObject*)error);
    if (arguments == nullptr)
    {
        PyErr_Clear();
        return;
    }
    call_reporter(handler, arguments);
    Py_DECREF((PyObject*)arguments);
}

void
report_rejection(IsolateState* state, PromiseRejectMessage message)
{
    const char* event_name = nullptr;
    switch (message.GetEvent())
    {
    case kPromiseRejectWithNoHandler:
        // A candidate only: this one is revoked below if a handler appears before
        // the checkpoint ends, and the host decides when to publish it.
        event_name = "unhandledrejection";
        break;
    case kPromiseHandlerAddedAfterReject:
        // The revocation, delivered only for promises V8 reported.
        event_name = "rejectionhandled";
        break;
    default:
        // Resolved/rejected after resolution: not a rejection a page observes.
        return;
    }
    Isolate* isolate = state->isolate();
    HandleScope handle_scope(isolate);
    Local<Context> context;
    if (!message.GetPromise()->GetCreationContext().ToLocal(&context)) return;
    auto* owner = context_from_v8(context);
    if (!context_is_active(owner) || owner->rejection_handler == nullptr) return;
    PyObject* handler = owner->rejection_handler;
    Py_INCREF((PyObject*)handler);
    PyOwnedRef keep_handler(handler);

    // The identity hash is stable for the lifetime of the promise and is what
    // the reporter keys its pending notifications on.
    PyObject* name = PyUnicode_FromString(event_name);
    PyObject* identity = nullptr;
    PyObject* promise = nullptr;
    Local<Promise> js_promise = message.GetPromise();
    if (!js_promise.IsEmpty())
    {
        identity = PyLong_FromLong(js_promise->GetIdentityHash());
        promise = py_from_js(js_promise, context);
    }
    PyObject* reason = to_python(message.GetValue(), context);
    PyObject* arguments = (name != nullptr && identity != nullptr && promise != nullptr &&
                           reason != nullptr)
        ? PyTuple_Pack(4, name, identity, promise, reason)
        : nullptr;
    Py_XDECREF((PyObject*)name);
    Py_XDECREF((PyObject*)identity);
    Py_XDECREF((PyObject*)promise);
    Py_XDECREF((PyObject*)reason);
    if (arguments == nullptr)
    {
        PyErr_Clear();
        return;
    }
    call_reporter(handler, arguments);
    Py_DECREF((PyObject*)arguments);
}

// Installed through ``error_hooks_install``; the isolate owns them for as long
// as they are installed, so they must stay file-static for removal to match.
void
on_uncaught_message(Local<Message> message, Local<Value> data)
{
    IsolateState* state = reporting_isolate();
    if (state != nullptr)
        report_uncaught(state, message, data);
}

void
on_promise_rejection(PromiseRejectMessage message)
{
    IsolateState* state = reporting_isolate();
    if (state != nullptr)
        report_rejection(state, message);
}

bool
require_callable(PyObject* callback, const char* name)
{
    if (callback == Py_None || PyCallable_Check(callback))
        return true;
    PyErr_Format(PyExc_TypeError, "%s must be callable or None", name);
    return false;
}

}  // namespace

bool
error_hooks_install(context_c* context, PyObject* on_uncaught, PyObject* on_rejection)
{
    auto* state = context->isolate_state;
    if (!require_callable(on_uncaught, "on_uncaught"))
        return false;
    if (!require_callable(on_rejection, "on_rejection"))
        return false;
    PyObject* uncaught = on_uncaught == Py_None ? nullptr : on_uncaught;
    PyObject* rejection = on_rejection == Py_None ? nullptr : on_rejection;
    Py_XINCREF((PyObject*)uncaught);
    Py_XINCREF((PyObject*)rejection);
    Py_CLEAR(context->uncaught_handler);
    Py_CLEAR(context->rejection_handler);
    context->uncaught_handler = uncaught;
    context->rejection_handler = rejection;
    if (state->error_hooks_installed())
        return true;
    // A second AddMessageListener would report every exception twice, so the
    // listeners are installed once and only the reporters are replaced.
    state->isolate()->AddMessageListener(CallbackBoundary<on_uncaught_message>::call);
    state->isolate()->SetPromiseRejectCallback(CallbackBoundary<on_promise_rejection>::call);
    state->set_error_hooks_installed(true);
    return true;
}

void
error_hooks_uninstall(IsolateState* state)
{
    if (!state->error_hooks_installed())
        return;
    state->set_error_hooks_installed(false);
    Isolate* isolate = state->isolate();
    if (isolate == nullptr)
        return;
    isolate->RemoveMessageListeners(CallbackBoundary<on_uncaught_message>::call);
    isolate->SetPromiseRejectCallback(nullptr);
}
