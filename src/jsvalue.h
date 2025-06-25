#ifndef js_value_H
#define js_value_H

#include <Python.h>
#include <v8.h>

using namespace v8;

typedef struct
{
    PyObject_HEAD
    Persistent<Value> value;
} js_value;

extern PyTypeObject *js_value_type;
int js_value_type_init();

js_value *js_value_new(Local<Value> value, Local<Context> context);
js_value *js_value_weak_new(Local<Value> value, Local<Context> context);
PyObject *js_value_fake_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void js_value_dealloc(js_value *self);

PyObject *js_value_repr(js_value *self);

#endif
