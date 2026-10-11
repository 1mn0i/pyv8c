#include <Python.h>
#include "pyv8.h"
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
    if (object->IsArrayBuffer() || object->IsSharedArrayBuffer()) {
        const bool shared = object->IsSharedArrayBuffer();
        if (!shared && object.As<ArrayBuffer>()->WasDetached())
            return true;
        std::shared_ptr<BackingStore> store = shared
            ? object.As<SharedArrayBuffer>()->GetBackingStore()
            : object.As<ArrayBuffer>()->GetBackingStore();
        if (store) {
            *data = static_cast<const char*>(store->Data());
            *length = static_cast<Py_ssize_t>(store->ByteLength());
        }
        return true;
    }
    if (object->IsArrayBufferView()) {
        Local<ArrayBufferView> view = object.As<ArrayBufferView>();
        std::shared_ptr<BackingStore> store = view->Buffer()->GetBackingStore();
        if (store) {
            *data = static_cast<const char*>(store->Data()) +
                    static_cast<size_t>(view->ByteOffset());
            *length = static_cast<Py_ssize_t>(view->ByteLength());
        }
        return true;
    }
    return true;
}

static int
js_buffer_getbuffer(PyObject* self, Py_buffer* view, int flags)
{
    (void)flags;
    const char* data = nullptr;
    Py_ssize_t length = 0;
    if (!current_range((js_buffer*)self, &data, &length))
        return -1;
    if (data == nullptr || length == 0) {
        // An empty range still needs an address Python may read from.
        view->buf = (void*)"";
        view->len = 0;
    }
    else {
        view->buf = (void*)data;
        view->len = length;
    }
    view->obj = self;
    Py_INCREF(self);
    view->readonly = 1;
    view->itemsize = 1;
    view->format = (char*)"B";
    view->ndim = 1;
    view->shape = nullptr;
    view->strides = nullptr;
    view->suboffsets = nullptr;
    view->internal = nullptr;
    return 0;
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
    {Py_bf_getbuffer, (void*)js_buffer_getbuffer},
    {Py_tp_doc, (void*)"v8 buffer bytes, read through without a copy"},
    {0, NULL} };

static PyType_Spec js_buffer_spec = {
    "pyv8.JSBuffer",
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
    std::shared_ptr<BackingStore> store;
    if (object->IsArrayBuffer())
        store = object.As<ArrayBuffer>()->GetBackingStore();
    else if (object->IsSharedArrayBuffer())
        store = object.As<SharedArrayBuffer>()->GetBackingStore();
    else if (object->IsArrayBufferView())
        store = object.As<ArrayBufferView>()->Buffer()->GetBackingStore();
    else {
        PyErr_SetString(PyExc_TypeError,
                        "JavaScript object holds no bytes: not a buffer or a view");
        return NULL;
    }
    if (!store) {
        PyErr_SetString(PyExc_TypeError,
                        "JavaScript buffer has no backing store to read");
        return NULL;
    }

    js_buffer* buffer = (js_buffer*)PyType_GenericAlloc((PyTypeObject*)js_buffer_type, 0);
    if (buffer == NULL)
        return NULL;
    buffer->store = new (std::nothrow) std::shared_ptr<BackingStore>(store);
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
