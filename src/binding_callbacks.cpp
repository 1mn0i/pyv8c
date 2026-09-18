#include <v8.h>
#include <Python.h>
#include <climits>
#include <sstream>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "idl_spec.h"
#include "pyclass_handlers_internal.h"
#include "faker/trace.h"

using namespace v8;

enum FlagConstructor {
    kDontAll = 0,
    kNew = 1 << 0,
    kCall = 1 << 1,
    kAll = kNew | kCall,
};

#define SETUP                                                         \
    Isolate* isolate = info.GetIsolate();                             \
    HandleScope hs(isolate);                                          \
    Local<Context> context = isolate->GetCurrentContext();            \
    Context::Scope cs(context);  



#define Info(T) const PropertyCallbackInfo<T> &info


static void
py_class_construct_impl(const FunctionCallbackInfo<Value>& info,
                        Local<Context> context,
                        py_class* self,
                        context_c* py_context)
{
    Isolate* isolate = info.GetIsolate();
    bool can_new = (self->constructor_behavior & FlagConstructor::kNew) != 0;
    bool can_call = (self->constructor_behavior & FlagConstructor::kCall) != 0;
    if (!can_call && !can_new) {
        info.GetIsolate()->ThrowException(Exception::TypeError(JSTR("Illegal constructor")));
        return;
    }
    if (!can_call && !info.IsConstructCall()) {
        std::ostringstream message;
        message << "Failed to construct '" << PyUnicode_AsUTF8_abi3(self->cls_name)
                << "': Please use the 'new' operator, this DOM object constructor "
                   "cannot be called as a function.";
        info.GetIsolate()->ThrowException(Exception::TypeError(JSTR(message.str().c_str())));
        return;
    }
    if (self->unconstructable) {
        PyObject* message = PyUnicode_FromFormat("%S is not a constructor", self->cls_name);
        if (message == NULL) {
            js_throw_py();
            return;
        }
        Local<Value> js_message = js_from_py(message, context);
        Py_DECREF((PyObject*)message);
        if (js_message.IsEmpty() || PyErr_Occurred()) {
            js_throw_py();
            return;
        }
        info.GetIsolate()->ThrowException(Exception::TypeError(js_message.As<String>()));
        return;
    }

    PyObject* kwargs = PyDict_New();
    if (kwargs == NULL) {
        js_throw_py();
        return;
    }
    if (PyDict_SetItemString(kwargs, "win", py_context->py_global) < 0) {
        Py_DECREF((PyObject*)kwargs);
        js_throw_py();
        return;
    }
    PyObject* args = pys_from_jss(info, context);
    if (args == NULL) {
        Py_DECREF((PyObject*)kwargs);
        js_throw_py();
        return;
    }
    PyObject* new_object = self->constructor_adapter != NULL
        ? PyObject_CallFunctionObjArgs(
            self->constructor_adapter, self->cls, args, kwargs, NULL)
        : PyObject_Call(self->cls, args, kwargs);
    Py_DECREF((PyObject*)args);
    Py_DECREF((PyObject*)kwargs);
    if (new_object == NULL) {
        js_throw_py();
        return;
    }
    if (!py_class_init_js_object(
            v8_compat::callback_holder(info), new_object, context)) {
        Py_DECREF((PyObject*)new_object);
        js_throw_py();
        return;
    }
    Py_DECREF((PyObject*)new_object);
}


void py_class_construct_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;

    py_class *self = (py_class *) IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    context_c* ctx_c = context_from_v8(context);
    if (ctx_c == nullptr)
        return;
    if (context_trace_enabled(ctx_c)) {
        {
            faker::TraceScope construct_trace(info, 3, self->cls_name, self->cls, ctx_c);
            py_class_construct_impl(info, context, self, ctx_c);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else {
        py_class_construct_impl(info, context, self, ctx_c);
    }
}

static void py_class_method_impl(const FunctionCallbackInfo<Value> &info, Local<Context> context,
                                 PyObject *receiver, MethodSpec *binding)
{
    PyOwnedRef callable(PyV8_NewRef(binding->callback));
    int offset = binding->pass_receiver && receiver != Py_None ? 1 : 0;
    PythonArguments args(info.Length() + offset);
    if (!args.valid() || !args.convert(info, context, offset ? receiver : nullptr)) {
        js_throw_py(); return;
    }
    PyObject *result = PyObject_Vectorcall(callable.get(), args.data(), args.size(), nullptr);
    if (result == nullptr)
    {
        js_throw_py();
        return;
    }
    Local<Value> converted = js_from_py(result, context);
    Py_DECREF((PyObject*)result);
    if (converted.IsEmpty() || PyErr_Occurred())
    {
        js_throw_py();
        return;
    }
    info.GetReturnValue().Set(converted);
}

void py_class_method_callback(const FunctionCallbackInfo<Value> &info)
{
    SETUP;
    if (!context_check_callback(context)) return;
    auto *binding = (MethodSpec *)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject *receiver = binding->pass_receiver ? get_self(info) : Py_None;
    if (receiver == nullptr)
    {
        js_throw_py();
        return;
    }
    context_c *owner = context_from_v8(context);
    if (owner == nullptr)
        return;
    if (context_trace_enabled(owner))
    {
        PyObject *trace_receiver = receiver;
        if (trace_receiver == Py_None && binding->trace_receiver)
            trace_receiver = binding->trace_receiver;
        {
            faker::TraceScope operation_trace(info, 2, binding->name, trace_receiver, owner);
            py_class_method_impl(info, context, receiver, binding);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else
    {
        py_class_method_impl(info, context, receiver, binding);
    }
}
// --- Interceptors ---
static void attribute_get_impl(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* self = binding->getter_receiver ? get_self(info) : Py_None;
    if (!self) { js_throw_py(); return; }
    PyOwnedRef keep_self(PyV8_NewRef(self));
    PyOwnedRef keep_binding(PyV8_NewRef((PyObject*)binding));
    PyObject* argv[] = {self};
    PyObject* result = PyObject_Vectorcall(binding->getter, argv, self == Py_None ? 0 : 1, nullptr);
    JS_PROPAGATE_PY(result);
    auto value = js_from_py(result, context);
    Py_DECREF((PyObject*)result);
    if (value.IsEmpty() || PyErr_Occurred()) { js_throw_py(); return; }
    info.GetReturnValue().Set(value);
}

void py_attribute_for_get(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* owner = context_from_v8(context);
    if (!context_trace_enabled(owner)) { attribute_get_impl(info); return; }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* receiver = binding->getter_receiver ? get_self(info) : Py_None;
    faker::TraceScope observation(info, 0, binding->name, receiver, owner);
    attribute_get_impl(info);
}

static void attribute_set_impl(const FunctionCallbackInfo<Value>& info, bool instance) {
    SETUP;
    if (!context_check_callback(context)) return;
    if (info.Length() < 1) {
        isolate->ThrowException(v8::Exception::TypeError(V8String(isolate, "1 argument required, but only 0 present")));
        return;
    }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* self = binding->setter_receiver ? get_self(info) : Py_None;
    if (!self) { js_throw_py(); return; }
    PyOwnedRef keep_self(PyV8_NewRef(self));
    PyOwnedRef keep_binding(PyV8_NewRef((PyObject*)binding));
    PyObject* value = py_argument_from_js(info[0], context);
    JS_PROPAGATE_PY(value);
    PyObject* argv[] = {self, value};
    PyObject* result = PyObject_Vectorcall(binding->setter, argv + (self == Py_None),
                                         self == Py_None ? 1 : 2, nullptr);
    Py_DECREF((PyObject*)value);
    JS_PROPAGATE_PY(result);
    Py_DECREF((PyObject*)result);
    if (instance && binding->replaceable) {
        const char* name = PyUnicode_AsUTF8_abi3(binding->name);
        if (name == nullptr) { js_throw_py(); return; }
        auto replaced = info.This()->CreateDataProperty(context, V8String(isolate, name), info[0]);
        if (replaced.IsNothing()) return;
    }
}

static void attribute_set(const FunctionCallbackInfo<Value>& info, bool instance) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* owner = context_from_v8(context);
    if (!context_trace_enabled(owner)) { attribute_set_impl(info, instance); return; }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* receiver = binding->setter_receiver ? get_self(info) : Py_None;
    faker::TraceScope observation(info, 1, binding->name, receiver, owner);
    attribute_set_impl(info, instance);
}

void py_attribute_for_instance_set(const FunctionCallbackInfo<Value>& info) { attribute_set(info, true); }
void py_attribute_for_property_set(const FunctionCallbackInfo<Value>& info) { attribute_set(info, false); }
