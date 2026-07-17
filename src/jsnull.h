#ifndef JS_NULL_H
#define JS_NULL_H

#include <Python.h>
#include <v8.h>

extern PyTypeObject* js_null_type;
extern PyObject* js_null_object;

typedef struct
{
    PyObject_HEAD
} js_null;

int js_null_type_init();

PyObject* js_null_repr(PyObject* self);
int js_null_bool(PyObject* self);

#endif
