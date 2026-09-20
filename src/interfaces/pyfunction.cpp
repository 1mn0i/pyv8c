#include <stdio.h>
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "context.h"
#include "convert.h"
#include "pyclass.h"
#include "pyfunction.h"
#include "idl_spec.h"
#include "trace.h"

using namespace v8;


PyTypeObject* py_function_type = NULL;

static int function_traverse(py_function* self, visitproc visit, void* arg) {
    Py_VISIT(self->function);
    Py_VISIT(self->function_name);
    Py_VISIT(self->receiver);
    return 0;
}
static int function_clear(py_function* self) {
    self->js_template.Reset();
    Py_CLEAR(self->function);
    Py_CLEAR(self->function_name);
    Py_CLEAR(self->receiver);
    return 0;
}

static PyType_Slot py_function_slots[] = {
    {Py_tp_traverse, (void*)PythonBoundary<function_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<function_clear>::call},
    {Py_tp_dealloc, (void*)PythonBoundary<py_function_dealloc>::call},
    {Py_tp_doc, (void*)""},
    {0, NULL} };

static PyType_Spec py_function_spec = {
    "pyv8.Function",
    sizeof(py_function),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
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
py_function_new(PyObject *function, context_c* owner)
{
    auto* isolate_state = owner->isolate_state;
    IN_ISOLATE(isolate_state);

    //py_function *self = (py_function *) py_function_type.tp_alloc(&py_function_type, 0);
    py_function* self = (py_function*)PyType_GenericAlloc(py_function_type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    Py_INCREF((PyObject*)function);
    self->function = function;
    if (PyMethod_Check_abi3(function)) {
        self->receiver = PyObject_GetAttrString(function, "__self__");
        if (!self->receiver) { Py_DECREF((PyObject*)self); return nullptr; }
    }
    PyObject* spec = idl_spec_for(function);
    if (spec == NULL && PyErr_Occurred()) { Py_DECREF((PyObject*)self); return NULL; }
    IdlSpec defaults;
    const IdlSpec& config = spec == nullptr ? defaults : *(IdlSpec*)spec;
    PyObject* name = config.name;
    if (name != NULL) {
        Py_INCREF((PyObject*)name);
        self->function_name = name;
    } else {
        self->function_name = PyObject_GetAttrString(function, "__name__");
    }
    int length = (int)config.length;
    bool throw_on_construct = spec != nullptr && (config.constructor_behavior & 1) == 0;
    Py_XDECREF((PyObject*)spec);
    if (self->function_name == NULL || PyErr_Occurred()) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    Local<External> js_self = isolate_state->callback_data(owner, (PyObject*)self);
    if (js_self.IsEmpty()) { Py_DECREF((PyObject*)self); return nullptr; }
    Local<FunctionTemplate> js_template;

    if (throw_on_construct) {
        js_template = FunctionTemplate::New(isolate, CallbackBoundary<py_function_callback>::call, js_self, Local<Signature>(), length, ConstructorBehavior::kThrow);
    }
    else {
        js_template = FunctionTemplate::New(isolate, CallbackBoundary<py_function_callback>::call, js_self, Local<Signature>(), length);
    }
    self->js_template.Set(isolate_state, isolate, js_template);
    return (PyObject *) self;
}

PyObject *
py_function_to_template(PyObject *func, context_c* owner)
{
    PyObject* cache = owner->function_template_cache;
    PyErr_PROPAGATE(cache);

    PyObject *templ = PyDict_GetItem(cache, func);
    if (templ != NULL) {
        return owned_template(templ);
    }

    templ = py_function_new(func, owner);
    PyErr_PROPAGATE(templ);
    if (PyDict_SetItem(cache, func, templ) < 0) {
        Py_DECREF((PyObject*)templ);
        return NULL;
    }
    Py_DECREF((PyObject*)templ);
    return owned_template(PyDict_GetItem(cache, func));
}

void
py_function_dealloc(py_function *self)
{
    PyObject_GC_UnTrack(self);
    function_clear(self);
    TP_FREE_ABI3(py_function_type, self);
}

Local<Function> 
py_template_to_function(py_function *self, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (self->isolate_state != isolate_state) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        return Local<Function>();
    }
    EscapableHandleScope hs(isolate);
    Local<Function> function;
    if (!without_gil([&] { return self->js_template.Get(isolate)->GetFunction(context).ToLocal(&function); })) {
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
    PythonArguments args(info.Length());
    if (!args.valid() || !args.convert(info, context)) { js_throw_py(); return; }
    PyOwnedRef callable(PyV8_NewRef(self->function));
    PyObject *result = PyObject_Vectorcall(callable.get(), args.data(), args.size(), nullptr);
    JS_PROPAGATE_PY(result);
    Local<Value> js_result = js_from_py(result, context);
    Py_XDECREF((PyObject*)result);
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

    py_function *self = (py_function *)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    context_c* owner = context.IsEmpty() ? nullptr :
        context_from_v8(context);
    if (owner == nullptr || !context_trace_enabled(owner)) {
        py_function_invoke(info, self, context);
        return;
    }
    // A Python-backed function is a binding-implemented operation, so calling it
    // is observed like any other host method: the receiver is the Python object
    // the callable belongs to (None for a plain function), the arguments are the
    // JavaScript ones and the value is whatever the call produced.
    PyObject* receiver = self->receiver ? self->receiver : Py_None;
    trace::TraceScope observation(info, 2, self->function_name, receiver, owner);
    py_function_invoke(info, self, context);
}
