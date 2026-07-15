#include <stdio.h>
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "context.h"
#include "convert.h"
#include "pyfunction.h"


PyTypeObject* py_function_type = NULL;

static PyType_Slot py_function_slots[] = {
    {Py_tp_dealloc, (void*)py_function_dealloc},
    {Py_tp_doc, ""},
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
    if (PyObject_HasAttrString(function, "__v8_name__")) {
        self->function_name = PyObject_GetAttrString(function, "__v8_name__");
    }
    else {
        self->function_name = PyObject_GetAttrString(function, "__name__");
    }
    if (self->function_name == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    Local<External> js_self = External::New(isolate, self);
    Local<FunctionTemplate> js_template;

    int length = 0;
    if (PyObject_HasAttrString(function, "__v8_length__"))
    {
        PyObject* len = PyObject_GetAttrString(function, "__v8_length__");
        if (PyNumber_Check(len))
            length = (int)PyLong_AsLong(len);
        Py_XDECREF(len);
    }

    bool throw_on_construct = false;
    if (PyObject_HasAttrString(function, "__v8_constructor_behavior__")) {
        PyObject* behavior = PyObject_GetAttrString(function, "__v8_constructor_behavior__");
        if (behavior == NULL) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        int truth = PyObject_IsTrue(behavior);
        Py_XDECREF(behavior);
        if (truth < 0) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        throw_on_construct = truth == 0;
    }

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
    Isolate* isolate = context->GetIsolate();
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
py_function_callback(const FunctionCallbackInfo<Value> &info)
{
    Isolate* isolate = info.GetIsolate();
    HandleScope hs(isolate);
    Local<Context> context = isolate->GetCurrentContext();

    py_function *self = (py_function *) info.Data().As<External>()->Value();
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
