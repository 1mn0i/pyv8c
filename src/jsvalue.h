#ifndef js_value_H
#define js_value_H

#include <Python.h>
#include <v8.h>

class IsolateState;

typedef struct
{
    PyObject_HEAD
    IsolateState* isolate_state;
    v8::Persistent<v8::Value> value;
    PyObject* py_context;
    PyObject* py_isolate;
    PyObject* registry_context;
    bool weak_owned;
} js_value;

extern PyTypeObject *js_value_type;
int js_value_type_init();

js_value *js_value_new(v8::Local<v8::Value> value, v8::Local<v8::Context> context);
js_value *js_value_weak_new(v8::Local<v8::Value> value, v8::Local<v8::Context> context);
PyObject *js_value_fake_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void js_value_dealloc(js_value *self);

PyObject *js_value_repr(js_value *self);

#endif
