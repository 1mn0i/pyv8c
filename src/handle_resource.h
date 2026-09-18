#ifndef PYV8_HANDLE_RESOURCE_H
#define PYV8_HANDLE_RESOURCE_H
#include "context.h"
#include "isolate_state.h"

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
