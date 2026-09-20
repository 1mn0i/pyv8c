#ifndef JS_TYPED_ARRAY_H
#define JS_TYPED_ARRAY_H

#include <Python.h>
#include <v8.h>
#include "jsobject.h"

typedef struct {
    js_object base;
} js_typedarray;

extern PyTypeObject* js_typedarray_type;
int js_typedarray_type_init();
void js_typedarray_dealloc(js_typedarray* self);
PyObject* js_typedarray_repr(js_typedarray* self);
PyObject* js_typearray_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);

#endif
