#include "microtask_queue.h"
#include "isolate.h"
#include "entry.h"
#ifdef V8_CPPGC_MICROTASK_QUEUE
#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
// V8's public cppgc headers use Clang's spelling even for MSVC consumers.
// This marker is only needed while parsing the header; no queue code uses it.
#define __builtin_frame_address(level) _AddressOfReturnAddress()
#endif
#include <cppgc/cross-thread-persistent.h>
#if defined(_MSC_VER) && !defined(__clang__)
#undef __builtin_frame_address
#endif
#endif

struct MicrotaskStorage {
#ifdef V8_CPPGC_MICROTASK_QUEUE
    cppgc::subtle::CrossThreadPersistent<v8::MicrotaskQueue> queue;
    v8::MicrotaskQueue* get() { return queue.Get(); }
#else
    std::unique_ptr<v8::MicrotaskQueue> queue;
    v8::MicrotaskQueue* get() { return queue.get(); }
#endif
    explicit MicrotaskStorage(v8::Isolate* isolate)
        : queue(v8::MicrotaskQueue::New(isolate, v8::MicrotasksPolicy::kExplicit)) {}
};

PyTypeObject* microtask_queue_type = nullptr;

bool microtask_queue_check(PyObject* object) {
    if (!object || object == Py_None) return true;
    if (!PyObject_TypeCheck(object, microtask_queue_type)) {
        PyErr_SetString(PyExc_TypeError, "microtask_queue must be a MicrotaskQueue"); return false;
    }
    auto* self = (microtask_queue_c*)object;
    if (!self->storage) {
        PyErr_SetString(PyExc_RuntimeError, "microtask queue is closed"); return false;
    }
    if (self->owner_thread != PyThread_get_thread_ident()) {
        PyErr_SetString(PyExc_RuntimeError, "microtask queue belongs to another owner thread"); return false;
    }
    return true;
}

v8::MicrotaskQueue* microtask_queue_get(PyObject* object, IsolateState* state) {
    if (!object || object == Py_None) return nullptr;
    if (!microtask_queue_check(object)) return nullptr;
    auto* self = (microtask_queue_c*)object;
    if (self->state != state) {
        PyErr_SetString(PyExc_ValueError, "microtask queue belongs to another isolate"); return nullptr;
    }
    return self->storage->get();
}

void microtask_queue_detach(PyObject* object) {
    if (object) --((microtask_queue_c*)object)->contexts;
}

static PyObject* queue_new(PyTypeObject* type, PyObject* args, PyObject* kwargs) {
    PyObject* owner;
    static const char* names[] = {"isolate", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", (char**)names, &owner)) return nullptr;
    auto* state = isolate_state_from_python(owner);
    if (!state) return nullptr;
    IN_ISOLATE(state);
    PyOwnedRef result(PyType_GenericAlloc(type, 0));
    if (!result.get()) return nullptr;
    auto* self = (microtask_queue_c*)result.get();
    self->storage = new MicrotaskStorage(isolate);
    self->py_isolate = PyV8_NewRef(owner);
    self->state = state;
    self->owner_thread = PyThread_get_thread_ident();
    state->retain_v8_handle();
    return PyV8_NewRef(result.get());
}

static void release_storage(microtask_queue_c* self) {
    // The strong Isolate owner prevents disposal until the counted root is gone.
    if (self->storage) {
        IN_ISOLATE(self->state);
        delete self->storage;
        self->storage = nullptr;
        self->state->release_v8_handle();
        self->state = nullptr;
    }
    Py_CLEAR(self->py_isolate);
}

static PyObject* queue_close(microtask_queue_c* self, PyObject*) {
    if (!self->storage) Py_RETURN_NONE;
    if (!microtask_queue_check((PyObject*)self)) return nullptr;
    if (self->contexts) {
        PyErr_SetString(PyExc_RuntimeError, "microtask queue still has associated contexts"); return nullptr;
    }
    release_storage(self);
    Py_RETURN_NONE;
}


static void queue_dealloc(microtask_queue_c* self) {
    release_storage(self);
    TP_FREE_ABI3(microtask_queue_type, self);
}
static PyMethodDef methods[] = {
    {"checkpoint", (PyCFunction)PythonBoundary<microtask_queue_checkpoint>::call, METH_VARARGS|METH_KEYWORDS, nullptr},
    {"close", (PyCFunction)PythonBoundary<queue_close>::call, METH_NOARGS, nullptr},
    {nullptr}
};
int microtask_queue_type_init() {
    PyType_Slot slots[] = {{Py_tp_new, (void*)PythonBoundary<queue_new>::call},
                          {Py_tp_dealloc, (void*)queue_dealloc},
                          {Py_tp_methods, methods}, {0, nullptr}};
    PyType_Spec spec = {"pyv8c.pyv8.MicrotaskQueue", sizeof(microtask_queue_c), 0, Py_TPFLAGS_DEFAULT, slots};
    microtask_queue_type = (PyTypeObject*)PyType_FromSpec(&spec);
    return microtask_queue_type ? 0 : -1;
}
