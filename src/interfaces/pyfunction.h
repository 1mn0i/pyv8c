#ifndef TEMPLATE_H
#define TEMPLATE_H

#include "bridge_base.h"
#include <Python.h>
#include <v8.h>

#include "v8_handle_slot.h"

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    PyObject *function;
    PyObject *function_name;
    PyObject *receiver;
    V8HandleSlot<v8::FunctionTemplate> js_template;
} py_function;

extern PyTypeObject* py_function_type;
int py_function_type_init();

void py_function_dealloc(py_function *self);
PyObject *py_function_new(PyObject *func, context_c* owner);

// Returns a reference of its own; the realm's cache keeps the template alive.
PyObject *py_function_to_template(PyObject *func, context_c* owner);
v8::Local<v8::Function> py_template_to_function(py_function *self, v8::Local<v8::Context> context);

#endif
