#include "bridge_base.h"
#include "buffer_range.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include <v8.h>

#include "context.h"
#include "handle_resource.h"
#include "jsbuffer.h"
#include "jsobject.h"

#include <memory>

using namespace v8;


PyTypeObject* js_buffer_type = NULL;


//: The range the wrapped object holds *right now*.  Nothing is cached: a detach makes every
//: cached address stale (Blink: "The raw_* fields may be stale after Detach. Use getters
//: instead.", dom_array_buffer_view.h:147-148), and a detached buffer answers no bytes at
//: all (pass_as_span.h:54-59).
static bool
current_range(js_buffer* self, const char** data, Py_ssize_t* length)
{
    *data = nullptr;
    *length = 0;
    if (!js_object_require_active(&self->base))
        return false;
    ExecutionScope execution((context_c*)self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base))
        return false;
    auto* isolate = execution.isolate();
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    auto range = buffer_range(object);
    *data = range.data();
    *length = static_cast<Py_ssize_t>(range.length);
    return true;
}

static int
js_buffer_getbuffer(PyObject* self, Py_buffer* view, int flags)
{
    const char* data = nullptr;
    Py_ssize_t length = 0;
    if (!current_range((js_buffer*)self, &data, &length))
        return -1;
    return PyBuffer_FillInfo(view, self, (void*)(data ? data : ""), length, 1, flags);
}

static void
js_buffer_dealloc(js_buffer* self)
{
    delete self->store;
    self->store = nullptr;
    js_object_dealloc((js_object*)self);
}

static PyType_Slot js_buffer_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_buffer_dealloc>::call},
    {Py_bf_getbuffer, (void*)PythonBoundary<js_buffer_getbuffer>::call},
    {Py_tp_doc, (void*)"v8 buffer bytes, read through without a copy"},
    {0, NULL} };

static PyType_Spec js_buffer_spec = {
    "pyv8c.pyv8.JSBuffer",
    sizeof(js_buffer),
    0,
    Py_TPFLAGS_DEFAULT,
    js_buffer_slots
};

int
js_buffer_type_init()
{
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
        return -1;
    PyTuple_SetItem(bases, 0, (PyObject*)js_object_type);
    Py_INCREF((PyObject*)js_object_type);  // PyTuple_SetItem steals a reference

    js_buffer_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_buffer_spec, bases);
    Py_DECREF(bases);
    if (js_buffer_type == NULL)
        return -1;
    return 0;
}

PyObject*
js_object_buffer(js_object* self, PyObject*)
{
    if (!js_object_require_active(self))
        return NULL;
    ExecutionScope execution((context_c*)self->registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(self)) return nullptr;
    auto* isolate = execution.isolate();
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    auto range = buffer_range(object);
    if (!range.is_buffer) {
        PyErr_SetString(PyExc_TypeError, "JavaScript object holds no bytes: not a buffer or a view");
        return nullptr;
    }
    if (!range.store) {
        PyErr_SetString(PyExc_TypeError, "JavaScript buffer has no backing store to read");
        return nullptr;
    }
    if (range.resizable()) {
        PyErr_SetString(PyExc_TypeError,
            "zero-copy buffer views require a non-resizable backing store; use bytes() instead");
        return nullptr;
    }

    js_buffer* buffer = (js_buffer*)PyType_GenericAlloc((PyTypeObject*)js_buffer_type, 0);
    if (buffer == NULL)
        return NULL;
    buffer->store = new (std::nothrow) std::shared_ptr<BackingStore>(range.store);
    if (buffer->store == nullptr) {
        Py_DECREF((PyObject*)buffer);
        PyErr_NoMemory();
        return NULL;
    }
    js_object_set_persistent((js_object*)buffer, object,
                             object->GetCreationContextChecked(), true);
    // Hand back the memoryview rather than the exporter: it is the plain Python object for
    // a buffer, and it keeps the exporter -- and through it the backing store -- alive for
    // as long as it is read.
    PyObject* view = PyMemoryView_FromObject((PyObject*)buffer);
    Py_DECREF((PyObject*)buffer);
    if (view == NULL)
        return NULL;
    return view;
}
