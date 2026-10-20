#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "runtime.h"
#include "crossing.h"
#include "error_family.h"
#include <v8.h>

#include "convert.h"
#include "jspromise.h"
#include "context.h"
#include "abi3.h"


using namespace v8;

PyTypeObject *js_promise_type = NULL;

static PyType_Slot js_promise_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_promise_dealloc>::call},
    {Py_tp_new, (void*)PythonBoundary<js_promise_new>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_promise_repr>::call},
    {Py_tp_doc, (void*)"v8 Promise"},
    {0, NULL} };

static PyType_Spec js_promise_spec = {
    "pyv8.JSPromise",
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
    Py_INCREF((PyObject*)js_object_type);

    js_promise_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_promise_spec, bases);
    Py_DECREF((PyObject*)bases);

    if (js_promise_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *
js_promise_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    if (!runtime_manager.ensure_initialized())
        return NULL;
    IsolateState* isolate_state = select_isolate(IsolateSelection::ActiveOrDefault);
    IN_ISOLATE(isolate_state);

    PyObject* resolve = NULL;
    PyObject* reject = NULL;
    static const char* keywords[] = { "resolve", "reject", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO", (char**)keywords, &resolve, &reject)) {
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
        Local<Object> exception = error_family::exception_as_value(resolve, context);
        Local<Value> js_resolve;
        if (!exception.IsEmpty())
            js_resolve = exception;
        else if (PyExceptionInstance_Check(resolve)) {
            // The rule the resolver settles by: a Python failure the table does not
            // declare is not a reason a realm may be given, so it goes back to the
            // caller that tried to settle with it rather than into JavaScript.
            Py_DECREF((PyObject*)self);
            PyErr_SetObject((PyObject*)Py_TYPE(resolve), resolve);
            return NULL;
        }
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
        Local<Object> exception = error_family::exception_as_value(reject, context);
        Local<Value> js_reject;
        if (!exception.IsEmpty())
            js_reject = exception;
        else if (PyExceptionInstance_Check(reject)) {
            Py_DECREF((PyObject*)self);
            PyErr_SetObject((PyObject*)Py_TYPE(reject), reject);
            return NULL;
        }
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
    Py_INCREF((PyObject*)promise_repr);
    return promise_repr;
}

void 
js_promise_dealloc(js_promise *self)
{
    js_object_dealloc((js_object*)self);
}


// ---------------------------------------------------------------- PromiseResolver

PyTypeObject *js_promise_resolver_type = NULL;

namespace
{

//: The error every method answers once the realm has closed.
const char* kRealmGone = "the realm this PromiseResolver belongs to is closed";

//: A resolver in a realm that can still run, or NULL with that error set.
bool resolver_context(js_promise_resolver* self, context_c** out)
{
    context_c* py_ctx = (context_c*)self->py_context;
    if (py_ctx == nullptr || !context_is_active(py_ctx)) {
        PyErr_SetString(PyExc_RuntimeError, kRealmGone);
        return false;
    }
    *out = py_ctx;
    return true;
}

//: What a resolve/reject call settles with.  A Python exception becomes the JavaScript
//: error the host's table declares, and one the table does not declare is not a reason a
//: realm may be given: it is raised to the caller that tried to settle with it.
Local<Value> settle_value(PyObject* value, Local<Context> context)
{
    Local<Object> exception = error_family::exception_as_value(value, context);
    if (!exception.IsEmpty())
        return exception;
    if (PyExceptionInstance_Check(value)) {
        PyErr_SetObject((PyObject*)Py_TYPE(value), value);
        return Local<Value>();
    }
    return js_from_py(value, context);
}

}

static int js_promise_resolver_traverse(js_promise_resolver* self, visitproc visit, void* arg) {
    Py_VISIT(self->py_context);
    Py_VISIT(self->py_isolate);
    return 0;
}

int js_promise_resolver_clear(js_promise_resolver* self) {
    IsolateCleanupScope cleanup(self->isolate_state, (PyObject*)self, (int (*)(PyObject*))js_promise_resolver_clear);
    if (!cleanup.ready()) return 0;
    self->registration.detach();
    self->resolver.Reset();
    self->isolate_state = nullptr;
    Py_CLEAR(self->py_context);
    Py_CLEAR(self->py_isolate);
    return 0;
}

static PyMethodDef js_promise_resolver_methods[] = {
    {"promise", (PyCFunction)PythonBoundary<js_promise_resolver_promise>::call, METH_NOARGS,
     "the promise this resolver settles"},
    {"resolve", (PyCFunction)PythonBoundary<js_promise_resolver_resolve>::call, METH_O,
     "settle it with a value (a Python exception crosses as a JavaScript error)"},
    {"reject", (PyCFunction)PythonBoundary<js_promise_resolver_reject>::call, METH_O,
     "reject it with a reason (a Python exception crosses as a JavaScript error)"},
    {NULL, NULL} };

static PyType_Slot js_promise_resolver_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_promise_resolver_dealloc>::call},
    {Py_tp_new, (void*)PythonBoundary<js_promise_resolver_new>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_promise_resolver_repr>::call},
    {Py_tp_traverse, (void*)PythonBoundary<js_promise_resolver_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<js_promise_resolver_clear>::call},
    {Py_tp_methods, js_promise_resolver_methods},
    {Py_tp_doc, (void*)"v8 Promise resolver: create a promise now, settle it later"},
    {0, NULL} };

static PyType_Spec js_promise_resolver_spec = {
    "pyv8.PromiseResolver",
    sizeof(js_promise_resolver),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    js_promise_resolver_slots };

int js_promise_resolver_type_init()
{
    js_promise_resolver_type = (PyTypeObject *)PyType_FromSpec(&js_promise_resolver_spec);
    if (js_promise_resolver_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *
js_promise_resolver_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    if (!runtime_manager.ensure_initialized())
        return NULL;
    PyObject* py_ctx;
    static const char* keywords[] = { "context", NULL };
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", (char**)keywords, &py_ctx)) {
        return NULL;
    }
    if (!PyObject_TypeCheck(py_ctx, context_type)) {
        PyErr_SetString(PyExc_TypeError, "context must be a Context instance");
        return NULL;
    }
    //: Unlike ``JSPromise`` this one enters the realm itself, so any thread may call it.
    ExecutionScope execution((context_c*)py_ctx);
    if (!execution) return nullptr;
    auto* isolate_state = ((context_c*)py_ctx)->isolate_state;
    auto* isolate = execution.isolate();
    auto context = execution.context();
    JS_TRY

    Local<Promise::Resolver> resolver;
    if (!Promise::Resolver::New(context).ToLocal(&resolver)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to create a JavaScript Promise resolver");
        return NULL;
    }
    js_promise_resolver *self = (js_promise_resolver *)PyType_GenericAlloc(type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    Py_INCREF(py_ctx);
    self->py_context = py_ctx;
    PyObject* py_isolate = ((context_c*)py_ctx)->py_isolate;
    if (py_isolate != nullptr) {
        Py_INCREF(py_isolate);
        self->py_isolate = py_isolate;
    }
    self->registration.attach((context_c*)py_ctx, (PyObject*)self,
                              (int (*)(PyObject*))js_promise_resolver_clear);
    self->resolver.Set(isolate_state, isolate, resolver);
    return (PyObject*)self;
}

PyObject *
js_promise_resolver_promise(js_promise_resolver* self, PyObject* unused)
{
    context_c* py_ctx;
    if (!resolver_context(self, &py_ctx)) return NULL;
    ExecutionScope execution(py_ctx);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    auto context = execution.context();
    JS_TRY

    Local<Promise::Resolver> resolver = self->resolver.Get(isolate);
    if (resolver.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, kRealmGone);
        return NULL;
    }
    //: A fresh handle on the same promise, so the caller may hold it as long as it likes.
    js_promise* result = (js_promise*)PyType_GenericAlloc(js_promise_type, 0);
    PyErr_PROPAGATE(result);
    js_object_set_persistent((js_object*)result, Local<Object>::Cast(resolver->GetPromise()),
                             context, true);
    return (PyObject*)result;
}

PyObject *
js_promise_resolver_resolve(js_promise_resolver* self, PyObject* value)
{
    context_c* py_ctx;
    if (!resolver_context(self, &py_ctx)) return NULL;
    ExecutionScope execution(py_ctx);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    auto context = execution.context();
    JS_TRY

    Local<Promise::Resolver> resolver = self->resolver.Get(isolate);
    if (resolver.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, kRealmGone);
        return NULL;
    }
    Local<Value> settle = settle_value(value, context);
    if (settle.IsEmpty()) return NULL;
    bool resolved;
    if (!resolver->Resolve(context, settle).To(&resolved)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to resolve JavaScript Promise");
        return NULL;
    }
    Py_RETURN_NONE;
}

PyObject *
js_promise_resolver_reject(js_promise_resolver* self, PyObject* value)
{
    context_c* py_ctx;
    if (!resolver_context(self, &py_ctx)) return NULL;
    ExecutionScope execution(py_ctx);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    auto context = execution.context();
    JS_TRY

    Local<Promise::Resolver> resolver = self->resolver.Get(isolate);
    if (resolver.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, kRealmGone);
        return NULL;
    }
    Local<Value> settle = settle_value(value, context);
    if (settle.IsEmpty()) return NULL;
    bool rejected;
    if (!resolver->Reject(context, settle).To(&rejected)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to reject JavaScript Promise");
        return NULL;
    }
    Py_RETURN_NONE;
}

PyObject *
js_promise_resolver_repr(js_promise_resolver* self)
{
    return PyUnicode_FromString(self->resolver.IsEmpty() ? "<PromiseResolver closed>"
                                                         : "<PromiseResolver>");
}

void
js_promise_resolver_dealloc(js_promise_resolver *self)
{
    PyObject_GC_UnTrack(self);
    js_promise_resolver_clear(self);
    if (Py_REFCNT((PyObject*)self) != 0) return;
    TP_FREE_ABI3(js_promise_resolver_type, self);
}

