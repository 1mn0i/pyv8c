#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jspromise.h"
#include "context.h"


using namespace v8;

PyTypeObject *js_promise_type = NULL;

static PyType_Slot js_promise_slots[] = {
    {Py_tp_dealloc, (void*)js_promise_dealloc},
    {Py_tp_new, (void*)js_promise_new},
    {Py_tp_repr, (void*)js_promise_repr},
    {Py_tp_doc, (void*)"v8 Promise"},
    {0, NULL} };

static PyType_Spec js_promise_spec = {
    "_pyv8.Promise",
    sizeof(js_promise),
    0,
    Py_TPFLAGS_DEFAULT,
    js_promise_slots };

int js_promise_type_init()
{
    // Create a base class spec
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    PyTuple_SetItem(bases, 0, (PyObject*)js_object_type);
    Py_INCREF(js_object_type);

    js_promise_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_promise_spec, bases);
    Py_DECREF(bases);

    //js_promise_type = (PyTypeObject*)PyType_FromSpec(&js_promise_spec);
    if (js_promise_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *
js_promise_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    if (!runtime_manager.require_initialized())
        return NULL;
    IsolateState* isolate_state = current_isolate_state();
    if (isolate_state == nullptr)
        isolate_state = runtime_manager.default_isolate();
    IN_ISOLATE(isolate_state);

    PyObject* resolve = NULL;
    PyObject* reject = NULL;
    static const char* keywords[] = { "resolve", "reject", NULL};
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "|OO", (char**)keywords, &resolve, &reject) < 0) {
        return NULL;
    }
    Local<Context> current_context = isolate->GetCurrentContext();
    if (current_context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "JSPromise requires an active V8 Context");
        return NULL;
    }
    IN_CONTEXT(current_context);
    //js_object* self = (js_object*)type->tp_alloc(type, 0);
    js_promise *self = (js_promise *)PyType_GenericAlloc(type, 0);
    PyErr_PROPAGATE(self);
    Local<Promise::Resolver> resolver;
    if (!Promise::Resolver::New(context).ToLocal(&resolver)) {
        Py_DECREF((PyObject*)self);
        PyErr_SetString(PyExc_RuntimeError, "failed to create JavaScript Promise resolver");
        return NULL;
    }
    Local<Promise> promise = resolver->GetPromise();
    if (resolve != NULL) {
        Local<Object> exception = pyexception_to_jsexception(resolve, context);
        Local<Value> js_resolve;
        if (!exception.IsEmpty())
            js_resolve = exception;
        else {
            js_resolve = js_from_py(resolve, context);
            if (PyErr_Occurred()) {
                Py_DECREF((PyObject*)self);
                return NULL;
            }
        }
        bool resolved;
        if (!resolver->Resolve(context, js_resolve).To(&resolved)) {
            Py_DECREF((PyObject*)self);
            PyErr_SetString(PyExc_RuntimeError, "failed to resolve JavaScript Promise");
            return NULL;
        }

    }
    if (reject != NULL) {
        Local<Object> exception = pyexception_to_jsexception(reject, context);
        Local<Value> js_reject;
        if (!exception.IsEmpty())
            js_reject = exception;
        else {
            js_reject = js_from_py(reject, context);
            if (PyErr_Occurred()) {
                Py_DECREF((PyObject*)self);
                return NULL;
            }
        }
        bool rejected;
        if (!resolver->Reject(context, js_reject).To(&rejected)) {
            Py_DECREF((PyObject*)self);
            PyErr_SetString(PyExc_RuntimeError, "failed to reject JavaScript Promise");
            return NULL;
        }
    }
    js_object_set_persistent((js_object*)self, promise, context, true);
    return (PyObject*)self;
}

PyObject * 
js_promise_repr(js_promise* self)
{
    if (!js_object_require_active(&self->base)) return nullptr;
    static PyObject* promise_repr = NULL;
    if (promise_repr == NULL)
    {
        promise_repr = PyUnicode_InternFromString("[object Promise]");
    }
    Py_INCREF(promise_repr);
    return promise_repr;
}

void 
js_promise_dealloc(js_promise *self)
{
    js_object_dealloc((js_object*)self);
}
