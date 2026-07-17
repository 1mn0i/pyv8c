#ifndef JS_FUNCTION_H
#define JS_FUNCTION_H

#include <Python.h>
#include <v8.h>
#include "jsobject.h"

typedef struct {
    js_object base;
    v8::Persistent<v8::Value> js_this;
} js_function;

extern PyTypeObject* js_function_type;
int js_function_type_init();
PyObject *js_function_call(js_function *self, PyObject *args, PyObject *kwargs);
PyObject *js_function_new(js_function *self, PyObject *args);
PyObject* js_function_repr(js_function* self);
void js_function_dealloc(js_function *self);

#endif
