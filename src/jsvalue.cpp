
#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <sstream>
#include <iostream>

#include "jsvalue.h"
#include "timeout_guard.h"

using namespace v8;

static PyObject* js_value_string(js_value* self, PyObject*) {
    if (!self->isolate_state || !self->isolate_state->require_active()) return nullptr;
    IN_ISOLATE(self->isolate_state);
    auto* owner = (context_c*)self->py_context;
    if (owner == nullptr || owner->js_context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value context is no longer active");
        return nullptr;
    }
    IN_CONTEXT(owner->js_context.Get(isolate));
    JS_TRY
    Local<String> result;
    if (!self->value.Get(isolate)->ToString(context).ToLocal(&result)) {
        PY_PROPAGATE_JS;
        return nullptr;
    }
    String::Utf8Value text(isolate, result);
    return PyUnicode_DecodeUTF8(*text, text.length(), "replace");
}

static PyObject* js_value_number(js_value* self, PyObject*) {
    if (!self->isolate_state || !self->isolate_state->require_active()) return nullptr;
    IN_ISOLATE(self->isolate_state);
    auto* owner = (context_c*)self->py_context;
    if (owner == nullptr || owner->js_context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value context is no longer active");
        return nullptr;
    }
    IN_CONTEXT(owner->js_context.Get(isolate));
    JS_TRY
    Local<Number> result;
    if (!self->value.Get(isolate)->ToNumber(context).ToLocal(&result)) {
        PY_PROPAGATE_JS;
        return nullptr;
    }
    return PyFloat_FromDouble(result->Value());
}

static PyObject* js_value_boolean(js_value* self, PyObject*) {
    if (!self->isolate_state || !self->isolate_state->require_active()) return nullptr;
    IN_ISOLATE(self->isolate_state);
    return PyBool_FromLong(self->value.Get(isolate)->BooleanValue(isolate));
}

static PyMethodDef js_value_methods[] = {
    {"_to_string", (PyCFunction)js_value_string, METH_NOARGS, nullptr},
    {"_to_number", (PyCFunction)js_value_number, METH_NOARGS, nullptr},
    {"_to_boolean", (PyCFunction)js_value_boolean, METH_NOARGS, nullptr},
    {nullptr}
};

static PyType_Slot js_value_slots[] = {
    {Py_tp_methods, (void*)js_value_methods},
    {Py_tp_dealloc, (void *)js_value_dealloc},
    {Py_tp_repr, (void *)js_value_repr},
    {Py_tp_doc, (void*)"v8 Value"},
    {0, NULL}};

static PyType_Spec js_value_spec = {
    "_pyv8.JSValue",
    sizeof(js_value),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    js_value_slots};

PyTypeObject *js_value_type = NULL;

int js_value_type_init()
{
    js_value_type = (PyTypeObject *)PyType_FromSpec(&js_value_spec);
    if (js_value_type == NULL)
    {
        return -1;
    }
    return 0;
}

js_value *
js_value_new(Local<Value> value, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_value *self;

    self = (js_value*)PyType_GenericAlloc(js_value_type, 0);

    if (self != NULL) {
        self->isolate_state = isolate_state;
        self->value.Reset(isolate, value);
        isolate_state->retain_v8_handle();
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            context_c* py_context = (context_c*)v8_compat::external_value(context_data);
            self->py_context = (PyObject*)py_context;
            Py_INCREF(self->py_context);
            if (py_context->py_isolate != nullptr) {
                self->py_isolate = py_context->py_isolate;
                Py_INCREF(self->py_isolate);
            }
        }
    }
    return self;
}

void js_value_weak_callback(const WeakCallbackInfo<js_value> &info)
{
    js_value *self = info.GetParameter();
    if (!self->value.IsEmpty()) {
        self->value.Reset();
        self->isolate_state->release_v8_handle();
    }
    Py_XDECREF(self);
}

js_value *
js_value_weak_new(Local<Value> value, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_value*self;
    self = (js_value*)PyType_GenericAlloc(js_value_type, 0);
    if (self != NULL) {
        self->isolate_state = isolate_state;
        self->value.Reset(isolate, value);
        isolate_state->retain_v8_handle();
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            context_c* py_context = (context_c*)v8_compat::external_value(context_data);
            if (py_context->py_isolate != nullptr) {
                self->py_isolate = py_context->py_isolate;
                Py_INCREF(self->py_isolate);
            }
        }
        Py_INCREF(self);
        //self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kFinalizer);
        self->value.SetWeak(self, js_value_weak_callback, WeakCallbackType::kParameter);
    }
    return self;
}


PyObject *
js_value_repr(js_value *self)
{
    if (self->isolate_state == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value has no active V8 isolate");
        return NULL;
    }
    if (!self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);

    Local<Value> value = self->value.Get(isolate);
    if (value->IsBigInt()) {
        int64_t val = value.As<BigInt>()->Int64Value();
        std::ostringstream oss;
        oss << "[Bigint " << val << "]";
        std::string str = oss.str();
        return PyUnicode_FromString(str.c_str());
    }

    return PyUnicode_FromString("[Value toString]");
}

void 
js_value_dealloc(js_value *self)
{
    if (!self->value.IsEmpty()) {
        self->value.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    Py_XDECREF(self->py_context);
    Py_XDECREF(self->py_isolate);
    self->isolate_state = nullptr;
    TP_FREE_ABI3(js_value_type, self);
}
