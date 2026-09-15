#include <stdio.h>
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "context.h"
#include "convert.h"
#include "pyclass.h"
#include "pyfunction.h"
#include "idl_spec.h"
#include "faker/trace.h"

using namespace v8;


PyTypeObject* py_function_type = NULL;

static PyType_Slot py_function_slots[] = {
    {Py_tp_dealloc, (void*)py_function_dealloc},
    {Py_tp_doc, (void*)""},
    {0, NULL} };

static PyType_Spec py_function_spec = {
    "_pyv8env.Function",
    sizeof(py_function),
    0,
    Py_TPFLAGS_DEFAULT,
    py_function_slots };

int 
py_function_type_init()
{
    py_function_type = (PyTypeObject *)PyType_FromSpec(&py_function_spec);
    if (py_function_type == NULL)
    {
        return -1;
    }
    return 0;
}

static void py_function_callback(const FunctionCallbackInfo<Value> &info);

PyObject *
py_function_new(PyObject *function, IsolateState* isolate_state)
{
    IN_ISOLATE(isolate_state);

    //py_function *self = (py_function *) py_function_type.tp_alloc(&py_function_type, 0);
    py_function* self = (py_function*)PyType_GenericAlloc(py_function_type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    self->js_template = new Persistent<FunctionTemplate>();
    Py_INCREF(function);
    self->function = function;
    PyObject* spec = idl_spec_for(function);
    if (spec == NULL && PyErr_Occurred()) { Py_DECREF((PyObject*)self); return NULL; }
    IdlSpec defaults;
    const IdlSpec& config = spec == nullptr ? defaults : *(IdlSpec*)spec;
    PyObject* name = config.name;
    if (name != NULL) {
        Py_INCREF(name);
        self->function_name = name;
    } else {
        self->function_name = PyObject_GetAttrString(function, "__name__");
    }
    int length = (int)config.length;
    bool throw_on_construct = spec != nullptr && (config.constructor_behavior & 1) == 0;
    Py_XDECREF(spec);
    if (self->function_name == NULL || PyErr_Occurred()) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    Local<External> js_self = v8_compat::external_new(isolate, self);
    Local<FunctionTemplate> js_template;

    if (throw_on_construct) {
        js_template = FunctionTemplate::New(isolate, py_function_callback, js_self, Local<Signature>(), length, ConstructorBehavior::kThrow);
    }
    else {
        js_template = FunctionTemplate::New(isolate, py_function_callback, js_self, Local<Signature>(), length);
    }
    self->js_template->Reset(isolate, js_template);
    return (PyObject *) self;
}

PyObject *
py_function_to_template(PyObject *func, IsolateState* isolate_state)
{
    PyObject* cache = isolate_state->function_template_cache();
    PyErr_PROPAGATE(cache);

    PyObject *templ = PyDict_GetItem(cache, func);
    if (templ != NULL) {
        return templ;
    }

    templ = py_function_new(func, isolate_state);
    PyErr_PROPAGATE(templ);
    if (PyDict_SetItem(cache, func, templ) < 0) {
        Py_DECREF(templ);
        return NULL;
    }
    Py_DECREF(templ);
    return PyDict_GetItem(cache, func);
}

void
py_function_dealloc(py_function *self)
{
    if (self->js_template != NULL) {
        self->js_template->Reset();
        delete self->js_template;
    }
    Py_XDECREF(self->function);
    Py_XDECREF(self->function_name);
    TP_FREE_ABI3(py_function_type, self);
}

Local<Function> 
py_template_to_function(py_function *self, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (self->isolate_state != isolate_state) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return Local<Function>();
    }
    EscapableHandleScope hs(isolate);
    Local<Function> function;
    if (!self->js_template->Get(isolate)->GetFunction(context).ToLocal(&function)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript function template");
        return Local<Function>();
    }
    Local<Value> function_name = js_from_py(self->function_name, context);
    if (PyErr_Occurred()) {
        return Local<Function>();
    }
    if (!function_name.IsEmpty() && function_name->IsString()) {
        function->SetName(function_name.As<String>());
    }
    return hs.Escape(function);
}

static void
py_function_invoke(const FunctionCallbackInfo<Value> &info, py_function *self, Local<Context> context)
{
    PyObject *args = pys_from_jss(info, context);
    JS_PROPAGATE_PY(args);
    PyObject *result = PyObject_CallObject(self->function, args);
    Py_XDECREF(args);
    JS_PROPAGATE_PY(result);
    Local<Value> js_result = js_from_py(result, context);
    Py_XDECREF(result);
    if (js_result.IsEmpty()) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_RuntimeError, "failed to convert Python callback result");
        }
        js_throw_py();
        return;
    }
    info.GetReturnValue().Set(js_result);
}

static void
py_function_callback(const FunctionCallbackInfo<Value> &info)
{
    Isolate* isolate = info.GetIsolate();
    HandleScope hs(isolate);
    Local<Context> context = isolate->GetCurrentContext();

    py_function *self = (py_function *) v8_compat::external_value(info.Data());
    context_c* owner = context.IsEmpty() ? nullptr :
        v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (owner == nullptr || !context_trace_enabled(owner)) {
        py_function_invoke(info, self, context);
        return;
    }
    // A Python-backed function is a binding-implemented operation, so calling it
    // is observed like any other host method: the receiver is the Python object
    // the callable belongs to (None for a plain function), the arguments are the
    // JavaScript ones and the value is whatever the call produced.
    PyObject* receiver = PyMethod_Check_abi3(self->function)
        ? PyMethod_GET_SELF_abi3(self->function) : Py_None;
    faker::TraceScope observation(info, 2, self->function_name, receiver, owner);
    py_function_invoke(info, self, context);
}
