#ifndef PYV8_ISOLATE_H
#define PYV8_ISOLATE_H

#include <Python.h>

#include "runtime.h"

typedef struct
{
    PyObject_HEAD
    IsolateState* state;
} isolate_c;

extern PyTypeObject* isolate_type;

int isolate_type_init();
IsolateState* isolate_state_from_python(PyObject* object, bool allow_none = false);

#endif
