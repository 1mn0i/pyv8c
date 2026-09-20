#include "pyv8.h"

#include "context.h"
#include "isolate.h"

using namespace v8;

static PyObject* isolate_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);
static void isolate_dealloc(isolate_c* self);
static PyObject* isolate_close(isolate_c* self, PyObject* args);
static PyObject* isolate_context(isolate_c* self, PyObject* args, PyObject* kwargs);
static PyObject* isolate_gc(isolate_c* self, PyObject* args);
static PyObject* isolate_get_closed(isolate_c* self, void* closure);

static PyMethodDef isolate_methods[] = {
    {"close", (PyCFunction)PythonBoundary<isolate_close>::call, METH_NOARGS, "close this V8 isolate"},
    {"context", (PyCFunction)PythonBoundary<isolate_context>::call, METH_VARARGS | METH_KEYWORDS,
     "create a Context owned by this isolate"},
    {"gc", (PyCFunction)PythonBoundary<isolate_gc>::call, METH_NOARGS, "request a full V8 garbage collection"},
    {nullptr},
};

static PyGetSetDef isolate_getset[] = {
    {(char*)"closed", (getter)PythonBoundary<isolate_get_closed>::call, nullptr, nullptr, nullptr},
    {nullptr},
};

static PyType_Slot isolate_slots[] = {
    {Py_tp_new, (void*)PythonBoundary<isolate_new>::call},
    {Py_tp_dealloc, (void*)PythonBoundary<isolate_dealloc>::call},
    {Py_tp_methods, isolate_methods},
    {Py_tp_getset, isolate_getset},
    {Py_tp_doc, (void*)"independent V8 isolate"},
    {0, nullptr},
};

static PyType_Spec isolate_spec = {
    "_pyv8.Isolate",
    sizeof(isolate_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    isolate_slots,
};

PyTypeObject* isolate_type = nullptr;

int
isolate_type_init()
{
    isolate_type = (PyTypeObject*)PyType_FromSpec(&isolate_spec);
    return isolate_type == nullptr ? -1 : 0;
}

IsolateState*
isolate_state_from_python(PyObject* object, bool allow_none)
{
    if (allow_none && (object == nullptr || object == Py_None))
        return runtime_manager.default_isolate();
    if (object == nullptr || !PyObject_TypeCheck(object, isolate_type))
    {
        PyErr_SetString(PyExc_TypeError, "isolate must be an Isolate instance");
        return nullptr;
    }
    isolate_c* py_isolate = (isolate_c*)object;
    if (py_isolate->state == nullptr || !py_isolate->state->is_active())
    {
        PyErr_SetString(PyExc_RuntimeError, "V8 isolate has been closed");
        return nullptr;
    }
    return py_isolate->state;
}

static PyObject*
isolate_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    static const char* keywords[] = {nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "", (char**)keywords))
        return nullptr;

    IsolateState* state = runtime_manager.create_isolate();
    if (state == nullptr)
        return nullptr;

    isolate_c* self = (isolate_c*)PyType_GenericAlloc(type, 0);
    if (self == nullptr)
    {
        runtime_manager.destroy_isolate(state, false);
        return nullptr;
    }
    self->state = state;
    state->python_owner = (PyObject*)self;
    return (PyObject*)self;
}

static void
isolate_dealloc(isolate_c* self)
{
    if (self->state != nullptr)
    {
        self->state->python_owner = nullptr;
        runtime_manager.destroy_isolate(self->state, false);
        self->state = nullptr;
        PyErr_Clear();
    }
    TP_FREE_ABI3(isolate_type, self);
}

static PyObject*
isolate_close(isolate_c* self, PyObject* args)
{
    if (self->state == nullptr)
        PYV8_RETURN_FALSE;
    if (!runtime_manager.destroy_isolate(self->state, true))
        return nullptr;
    self->state = nullptr;
    PYV8_RETURN_TRUE;
}

static PyObject*
isolate_context(isolate_c* self, PyObject* args, PyObject* kwargs)
{
    if (isolate_state_from_python((PyObject*)self) == nullptr)
        return nullptr;

    PyObject* call_kwargs = kwargs == nullptr ? PyDict_New() : PyDict_Copy(kwargs);
    if (call_kwargs == nullptr)
        return nullptr;
    if (PyDict_SetItemString(call_kwargs, "isolate", (PyObject*)self) < 0)
    {
        Py_DECREF((PyObject*)call_kwargs);
        return nullptr;
    }
    PyObject* result = PyObject_Call((PyObject*)context_type, args, call_kwargs);
    Py_DECREF((PyObject*)call_kwargs);
    return result;
}

static PyObject*
isolate_gc(isolate_c* self, PyObject* args)
{
    IsolateState* state = isolate_state_from_python((PyObject*)self);
    if (state == nullptr)
        return nullptr;
    IN_ISOLATE(state);
    isolate->LowMemoryNotification();
    PYV8_RETURN_NONE;
}

static PyObject*
isolate_get_closed(isolate_c* self, void* closure)
{
    return PyBool_FromLong(self->state == nullptr || !self->state->is_active());
}
