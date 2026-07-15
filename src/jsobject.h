#ifndef JS_OBJECT_H
#define JS_OBJECT_H

#include <Python.h>
#include <v8.h>

using namespace v8;

class IsolateState;

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    Persistent<Object> object;
    PyObject* py_context;
    PyObject* py_isolate;
} js_object;

extern PyTypeObject* js_object_type;
int js_object_type_init();

js_object *js_object_new(Local<Object> object, Local<Context> context);
js_object *js_object_weak_new(Local<Object> object, Local<Context> context);
void js_object_set_persistent(js_object* self, Local<Object> object,
                              Local<Context> context, bool retain_context);
PyObject *js_object_fake_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void js_object_dealloc(js_object *self);

PyObject *js_object_getattro(js_object *self, PyObject *name);
int js_object_setattro(js_object *self, PyObject *name, PyObject *value);
PyObject *js_object_getiter(js_object *self);
Py_ssize_t js_object_length(js_object *self);
PyObject *js_object_dir(js_object *self);
PyObject *js_object_repr(js_object *self);
PyObject* js_object_eq(js_object* self, PyObject* other);


#endif
