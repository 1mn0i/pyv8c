#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "crossing.h"
#include <v8.h>
#include <climits>
#include <algorithm>

#include "jsfunction.h"
#include "jsobject.h"
#include "convert.h"
#include "context.h"
#include "watchdog.h"

using namespace v8;

PyTypeObject *js_function_type = NULL;

static PyObject* js_function_trace_source(js_function* self, PyObject*) {
    if (!js_object_require_active(&self->base)) return nullptr;
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    Local<String> source;
    // Use the engine's function source rather than the page's toString method.
    if (!object.As<Function>()->FunctionProtoToString(context).ToLocal(&source)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to preview function source");
        return nullptr;
    }
    constexpr uint32_t limit = 160;
    const uint32_t count = std::min<uint32_t>(source->Length(), limit);
    uint16_t buffer[limit];
    source->Write(isolate, 0, count, buffer);
    int byte_order = -1;
    PyObject* text = PyUnicode_DecodeUTF16(reinterpret_cast<const char*>(buffer),
                                         count * 2, "replace", &byte_order);
    if (text != nullptr && source->Length() > static_cast<int>(limit)) {
        PyObject* clipped = PyUnicode_FromFormat("%U...", text);
        Py_DECREF(text);
        return clipped;
    }
    return text;
}

static PyMethodDef js_function_methods[] = {
    {"_trace_source", (PyCFunction)PythonBoundary<js_function_trace_source>::call,
     METH_NOARGS, "Bounded engine source for trace formatting."},
    {nullptr, nullptr, 0, nullptr}
};

static PyType_Slot js_function_slots[] = {
    {Py_tp_methods, js_function_methods},
    {Py_tp_dealloc, (void*)PythonBoundary<js_function_dealloc>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_function_repr>::call},
    {Py_tp_call, (void*)PythonBoundary<js_function_call>::call},
    {Py_tp_doc, (void*)""},
    {0, NULL} };

static PyType_Spec js_function_spec = {
    "pyv8c.pyv8.JSFunction",
    sizeof(js_function),
    0,
    Py_TPFLAGS_DEFAULT,
    js_function_slots };

int js_function_type_init()
{
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    PyTuple_SetItem(bases, 0, (PyObject*)js_object_type);
    Py_INCREF((PyObject*)js_object_type);

    js_function_type = (PyTypeObject * )PyType_FromSpecWithBases(&js_function_spec, bases);
    Py_DECREF((PyObject*)bases);

    if (js_function_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *js_function_call(js_function *self, PyObject *args, PyObject *kwargs) {
    if (!js_object_require_active(&self->base))
        return NULL;
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY

    Local<Value> js_this;
    if (self->js_this.IsEmpty()) {
        js_this = Undefined(isolate);
    } else {
        js_this = self->js_this.Get(isolate);
    }
    // The tuple's length is a Py_ssize_t and every V8 entry point that takes one
    // takes it as an int, so the narrowing is made here, once, with a check
    // instead of a warning.
    const Py_ssize_t argument_count = PyTuple_Size(args);
    if (argument_count < 0)
        return NULL;
    if (argument_count > INT_MAX) {
        PyErr_SetString(PyExc_OverflowError, "too many arguments for a JavaScript call");
        return NULL;
    }
    const int argc = static_cast<int>(argument_count);
    MaybeLocal<Value> result;
    DeadlineScope timeout_guard(context);
    V8ValueArguments argv(argc);
    if (!argv.valid())
    {
        PyErr_NoMemory();
        return NULL;
    }
    jss_from_pys(args, argv.data(), context);
    if (PyErr_Occurred()) return NULL;
    timeout_guard.start();
    result = without_gil([&] { return object->CallAsFunction(context, js_this, argc, argv.data()); });
    timeout_guard.stop();
    PY_PROPAGATE_JS;
    Local<Value> js_result;
    if (!result.ToLocal(&js_result)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to call JavaScript function");
        return NULL;
    }
    return py_from_js(js_result, context);
}


PyObject* js_function_repr(js_function* self) {
    if (!js_object_require_active(&self->base))
        return NULL;
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    DeadlineScope timeout_guard(context);
    timeout_guard.start();
    String::Utf8Value str(isolate, object);
    PyObject* repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);

    timeout_guard.stop();

    PY_PROPAGATE_JS;
    return repr;
}

void js_function_dealloc(js_function *self) {
    js_object_dealloc((js_object*)self);
}
