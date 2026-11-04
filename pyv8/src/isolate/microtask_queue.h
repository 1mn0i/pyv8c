#ifndef PYV8_MICROTASK_QUEUE_H
#define PYV8_MICROTASK_QUEUE_H
#include "bridge_base.h"
#include "isolate_state.h"

struct MicrotaskStorage;
struct microtask_queue_c {
    PyObject_HEAD
    PyObject* py_isolate;
    IsolateState* state;
    MicrotaskStorage* storage;
    unsigned long owner_thread;
    Py_ssize_t contexts;
};
extern PyTypeObject* microtask_queue_type;
int microtask_queue_type_init();
PyObject* microtask_queue_checkpoint(microtask_queue_c*, PyObject*, PyObject*);
bool microtask_queue_check(PyObject* queue);
v8::MicrotaskQueue* microtask_queue_get(PyObject* queue, IsolateState* state);
void microtask_queue_detach(PyObject* queue);
#endif
