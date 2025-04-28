#ifndef JS_UNDEFINED_H
#define JS_UNDEFINED_H

#include <Python.h>
#include <v8.h>

using namespace v8;

extern PyTypeObject* js_undefined_type;
extern PyObject* js_undefined_object;

typedef struct
{
    PyObject_HEAD
} js_undefined;

int js_undefined_type_init();

PyObject* js_undefined_repr(PyObject* self);
int js_undefined_bool(PyObject* self);

#endif
