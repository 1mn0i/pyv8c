#ifndef PYV8_HANDLE_RESOURCE_H
#define PYV8_HANDLE_RESOURCE_H
#include "context.h"
#include "isolate_state.h"
#include "v8_handle_slot.h"

// How an object standing for a Python one carries it: the first internal field
// marks it, the second is the owner.
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
};
#endif
