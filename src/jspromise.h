#ifndef JS_PROMISE_H
#define JS_PROMISE_H

#include <Python.h>
#include <v8.h>
#include "jsobject.h"

using namespace v8;

typedef struct {
    js_object base;
} js_promise;

extern PyTypeObject* js_promise_type;
int js_promise_type_init();
void js_promise_dealloc(js_promise *self);
PyObject* js_promise_repr(js_promise* self);
PyObject* js_promise_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);

#endif
