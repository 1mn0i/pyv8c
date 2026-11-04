#ifndef js_value_H
#define js_value_H

#include "bridge_base.h"
#include <Python.h>
#include <v8.h>
#include "handle_resource.h"

class IsolateState;

typedef struct
{
    PyObject_HEAD
    IsolateState* isolate_state;
    V8HandleSlot<v8::Value> value;
    PyObject* py_context;
    PyObject* py_isolate;
    HandleRegistration registration;
} js_value;

extern PyTypeObject *js_value_type;
int js_value_type_init();

js_value *js_value_new(v8::Local<v8::Value> value, v8::Local<v8::Context> context);
void js_value_dealloc(js_value *self);

PyObject *js_value_repr(js_value *self);

#endif
