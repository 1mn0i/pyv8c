#include "boundary/guard.h"
#include "v8n/boundary.h"
#include "v8n/realm.h"

namespace v8n
{

const char*
trace_event_name(TraceEvent event)
{
    switch (event)
    {
        case TraceEvent::kGet:
            return "get";
        case TraceEvent::kSet:
            return "set";
        case TraceEvent::kCall:
            return "call";
        case TraceEvent::kConstruct:
            return "construct";
        case TraceEvent::kHas:
            return "has";
        case TraceEvent::kDescriptor:
            return "descriptor";
        case TraceEvent::kEnumerate:
            return "enumerate";
    }
    return "unknown";
}

bool
Realm::set_trace_hook(PyObject* hook)
{
    // Same reason as set_error_hooks(): after a close there is no root set left
    // to hold it.
    if (!require_active("set_trace_hook"))
        return false;

    if (hook != nullptr && !PyCallable_Check(hook))
    {
        raise(PyExc_TypeError, "the trace hook must be callable");
        return false;
    }

    // Registered before it is stored, so a hook that is installed and then has
    // its realm closed can still be released by the root set.
    if (hook != nullptr && !register_root(&trace_hook_))
        return false;

    PyObject* previous = trace_hook_;
    trace_hook_ = hook;
    Py_XINCREF(trace_hook_);
    if (previous != nullptr)
        Py_DECREF(previous);
    return true;
}

void
Realm::trace(TraceEvent event, const char* name, const char* detail)
{
    PyObject* hook = trace_hook_;
    if (hook == nullptr)
        return;

    // A trace hook must not be able to break the operation it is watching, so
    // its failure is reported and swallowed.  `raise` is used inside the guard's
    // report path, which is also why the error state is captured first.
    PyCallback guard("a trace hook");

    PyRef event_name(PyRef::adopt(PyUnicode_FromString(trace_event_name(event))));
    PyRef member(PyRef::adopt(PyUnicode_FromString(name == nullptr ? "" : name)));
    if (!event_name || !member)
    {
        PyErr_Clear();
        return;
    }
    PyRef text(PyRef::adopt(PyUnicode_FromString(detail == nullptr ? "" : detail)));
    if (!text)
    {
        PyErr_Clear();
        return;
    }

    PyRef result(PyRef::adopt(
        PyObject_CallFunctionObjArgs(hook, event_name.get(), member.get(), text.get(), nullptr)));
    if (!result)
    {
        // Reported, not raised: the trace hook is an observer, and an observer
        // that fails must not change what it observes.
        PyErr_WriteUnraisable(hook);
    }
}

}  // namespace v8n
