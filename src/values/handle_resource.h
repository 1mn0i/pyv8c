#ifndef PYV8_HANDLE_RESOURCE_H
#define PYV8_HANDLE_RESOURCE_H
#include "context.h"
#include "isolate_state.h"

// How a JavaScript object that stands for a Python one carries it: the first
// internal field says the object is one of those, the second is the owner.
//
// This is handle vocabulary -- it describes the object rather than the interface
// it was made from -- so it lives here, where every layer that has to recognise a
// host object can reach it.
enum PythonObjectInternalField {
    PYTHON_MAGIC_FIELD = 0,
    PYTHON_OWNER_FIELD = 1,
    OBJECT_INTERNAL_FIELDS = 2,
};

// Non-owning registration: the wrapper's tp_traverse exposes its Python owner.
struct HandleRegistration {
    context_c* context;
    PyObject* subject;
    void attach(context_c* owner, PyObject* wrapper, int (*clear)(PyObject*)) {
        context = owner;
        subject = wrapper;
        context_register_handle(owner, wrapper, clear);
    }
    void detach() {
        context_unregister_handle(context, subject);
        context = nullptr;
        subject = nullptr;
    }
    template<class T> void reset(v8::Persistent<T>& handle, IsolateState* state) {
        if (handle.IsEmpty()) return;
        handle.Reset();
        state->release_v8_handle();
    }
};
#endif
