#ifndef TEMPLATE_H
#define TEMPLATE_H

#include <Python.h>
#include <v8.h>

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    PyObject *function;
    PyObject *function_name;
    PyObject *receiver;
    v8::Persistent<v8::FunctionTemplate> *js_template;
} py_function;

extern PyTypeObject* py_function_type;
int py_function_type_init();

void py_function_dealloc(py_function *self);
PyObject *py_function_new(PyObject *func, context_c* owner);

PyObject *py_function_to_template(PyObject *func, context_c* owner);
v8::Local<v8::Function> py_template_to_function(py_function *self, v8::Local<v8::Context> context);

#endif
