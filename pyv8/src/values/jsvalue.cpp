
#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "runtime.h"
#include "crossing.h"
#include <v8.h>
#include <sstream>
#include <iostream>

#include "jsvalue.h"

using namespace v8;

static bool value_active(js_value* self) {
    if (!self->isolate_state || self->value.IsEmpty() ||
        (self->registration.context && !context_is_active((context_c*)self->registration.context))) {
        PyErr_SetString(PyExc_RuntimeError, "V8 value context is closed");
        return false;
    }
    return self->isolate_state->require_active();
}

static int value_traverse(js_value* self, visitproc visit, void* arg) {
    Py_VISIT(self->py_context);
    Py_VISIT(self->py_isolate);
    return 0;
}

static int value_clear(js_value* self) {
    auto* state = self->isolate_state;
    IsolateCleanupScope cleanup(state, (PyObject*)self, (int (*)(PyObject*))value_clear);
    if (!cleanup.ready()) return 0;
    self->registration.detach();
    self->value.Reset();
    self->isolate_state = nullptr;
    Py_CLEAR(self->py_context);
    Py_CLEAR(self->py_isolate);
    return 0;
}

static PyObject* js_value_string(js_value* self, PyObject*) {
    if (!value_active(self)) return nullptr;
    ExecutionScope execution((context_c*)self->registration.context, (PyObject*)self);
    if (!execution || !value_active(self)) return nullptr;
    auto* isolate = execution.isolate();
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
    if (!value_active(self)) return nullptr;
    ExecutionScope execution((context_c*)self->registration.context, (PyObject*)self);
    if (!execution || !value_active(self)) return nullptr;
    auto* isolate = execution.isolate();
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
    if (!value_active(self)) return nullptr;
    ExecutionScope execution((context_c*)self->registration.context, (PyObject*)self);
    if (!execution || !value_active(self)) return nullptr;
    auto* isolate = execution.isolate();
    return PyBool_FromLong(self->value.Get(isolate)->BooleanValue(isolate));
}

static PyMethodDef js_value_methods[] = {
    {"_to_string", (PyCFunction)PythonBoundary<js_value_string>::call, METH_NOARGS, nullptr},
    {"_to_number", (PyCFunction)PythonBoundary<js_value_number>::call, METH_NOARGS, nullptr},
    {"_to_boolean", (PyCFunction)PythonBoundary<js_value_boolean>::call, METH_NOARGS, nullptr},
    {nullptr}
};

static PyType_Slot js_value_slots[] = {
    {Py_tp_traverse, (void*)PythonBoundary<value_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<value_clear>::call},
    {Py_tp_methods, (void*)js_value_methods},
    {Py_tp_dealloc, (void *)PythonBoundary<js_value_dealloc>::call},
    {Py_tp_repr, (void *)PythonBoundary<js_value_repr>::call},
    {Py_tp_doc, (void*)"v8 Value"},
    {0, NULL}};

static PyType_Spec js_value_spec = {
    "pyv8c.pyv8.JSValue",
    sizeof(js_value),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC,
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
        self->value.Set(isolate_state, isolate, value);
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            context_c* py_context = context_from_v8(context);
            self->registration.attach(py_context, (PyObject*)self, (int (*)(PyObject*))value_clear);
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

PyObject *
js_value_repr(js_value *self)
{
    if (!value_active(self)) return nullptr;
    if (self->isolate_state == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value has no active V8 isolate");
        return NULL;
    }
    if (!self->isolate_state->require_active())
        return NULL;
    ExecutionScope execution((context_c*)self->registration.context, (PyObject*)self);
    if (!execution || !value_active(self)) return nullptr;
    auto* isolate = execution.isolate();

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
    PyObject_GC_UnTrack(self);
    value_clear(self);
    if (Py_REFCNT((PyObject*)self) != 0) return;
    TP_FREE_ABI3(js_value_type, self);
}
