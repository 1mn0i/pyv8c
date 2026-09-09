#ifndef PYV8_TRACE_H
#define PYV8_TRACE_H

#include "context.h"
#include "convert.h"
#include "jsobject.h"

namespace faker {

class TraceSilence {
public:
    explicit TraceSilence(context_c* owner) : owner(owner) { ++owner->trace_depth; }
    ~TraceSilence() { --owner->trace_depth; }
    TraceSilence(const TraceSilence&) = delete;
    TraceSilence& operator=(const TraceSilence&) = delete;
private:
    context_c* owner;
};

inline PyObject* trace_value(v8::Local<v8::Value> value, v8::Local<v8::Context> context) {
    if (!value->IsObject()) return py_from_js(value, context);
    auto object = value.As<v8::Object>();
    auto* state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    if (object->InternalFieldCount() == OBJECT_INTERNAL_FIELDS &&
        object->GetInternalField(PYTHON_MAGIC_FIELD) == state->v8_string(V8StringId::PythonObjectMagic)) {
        auto* result = (PyObject*)v8_compat::external_value(object->GetInternalField(PYTHON_OBJECT_FIELD));
        Py_XINCREF(result);
        return result;
    }
    return (PyObject*)js_object_new(object, context);
}

class TraceEvent {
public:
    TraceEvent(v8::Isolate* isolate, context_c* owner, PyObject* receiver)
        : isolate(isolate), owner(owner), receiver(receiver), caught(isolate),
          context(isolate->GetCurrentContext()) {}
    ~TraceEvent() { if (caught.HasCaught()) caught.ReThrow(); }
    TraceEvent(const TraceEvent&) = delete;
    TraceEvent& operator=(const TraceEvent&) = delete;
    bool failed() const { return caught.HasCaught(); }

    void emit(int kind, PyObject* name, v8::Local<v8::Value> value,
              const v8::FunctionCallbackInfo<v8::Value>* info = nullptr) {
        if (caught.HasTerminated() || isolate->IsExecutionTerminating()) return;
        TraceSilence silence(owner);
        PyObject* error_type = nullptr;
        PyObject* error_value = nullptr;
        PyObject* error_traceback = nullptr;
        PyErr_Fetch(&error_type, &error_value, &error_traceback);
        v8::TryCatch observer_errors(isolate);
        auto error = failed() ? caught.Exception() : v8::Undefined(isolate).As<v8::Value>();
        if (value.IsEmpty() || (failed() && kind != 1)) value = v8::Undefined(isolate);
        PyObject* arguments = Py_None;
        Py_INCREF(arguments);
        if (info != nullptr && kind >= 2) {
            Py_DECREF(arguments);
            arguments = PyTuple_New(info->Length());
            if (arguments != nullptr) {
                for (int index = 0; index < info->Length(); ++index) {
                    PyObject* argument = trace_value((*info)[index], context);
                    if (argument == nullptr) { Py_CLEAR(arguments); break; }
                    PyTuple_SetItem(arguments, index, argument);
                }
            }
        }
        PyObject* py_value = arguments == nullptr ? nullptr : trace_value(value, context);
        PyObject* py_error = py_value == nullptr ? nullptr : trace_value(error, context);
        if (name != nullptr && py_error != nullptr && owner->trace_callback != nullptr) {
            PyObject* result = PyObject_CallFunctionObjArgs(
                owner->trace_callback, PyTuple_GetItem(owner->trace_event_names, kind),
                receiver == nullptr ? Py_None : receiver, name, arguments, py_value, py_error,
                info != nullptr && info->IsConstructCall() ? Py_True : Py_False,
                owner->trace_context_type, nullptr);
            Py_XDECREF(result);
        }
        Py_XDECREF(arguments);
        Py_XDECREF(py_value);
        Py_XDECREF(py_error);
        PyErr_Clear();
        PyErr_Restore(error_type, error_value, error_traceback);
    }

private:
    v8::Isolate* isolate;
    context_c* owner;
    PyObject* receiver;
    v8::TryCatch caught;
    v8::Local<v8::Context> context;
};

class TraceScope {
public:
    TraceScope(const v8::FunctionCallbackInfo<v8::Value>& info, int kind,
              PyObject* name, PyObject* receiver, context_c* owner)
        : info(info), kind(kind), name(name), event(info.GetIsolate(), owner, receiver) {}
    ~TraceScope() {
        auto value = info.GetReturnValue().Get();
        if (kind == 1) value = info.Length() > 0 ? info[0] : v8::Undefined(info.GetIsolate()).As<v8::Value>();
        if (kind == 3 && info.IsConstructCall() && (value.IsEmpty() || !value->IsObject()))
            value = info.This();
        event.emit(kind, name, value, &info);
    }
private:
    const v8::FunctionCallbackInfo<v8::Value>& info;
    int kind;
    PyObject* name;
    TraceEvent event;
};

}
#endif
