#ifndef JS_OBJECT_H
#define JS_OBJECT_H

#include <Python.h>
#include <v8.h>
#include "handle_resource.h"

class IsolateState;

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    v8::Persistent<v8::Object> object;
    PyObject* py_context;
    PyObject* py_isolate;
    HandleRegistration registration;
    DeferredPythonRelease deferred_release;
    bool weak_owned;
} js_object;

extern PyTypeObject* js_object_type;
int js_object_type_init();

js_object *js_object_new(v8::Local<v8::Object> object, v8::Local<v8::Context> context);
js_object *js_object_weak_new(v8::Local<v8::Object> object, v8::Local<v8::Context> context);
void js_object_set_persistent(js_object* self, v8::Local<v8::Object> object,
                              v8::Local<v8::Context> context, bool retain_context);
bool js_object_require_active(const js_object* self);
PyObject *js_object_fake_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void js_object_dealloc(js_object *self);
int js_object_clear(js_object* self);
int js_object_traverse(js_object* self, visitproc visit, void* arg);

PyObject *js_object_getattro(js_object *self, PyObject *name);
int js_object_setattro(js_object *self, PyObject *name, PyObject *value);
PyObject *js_object_getiter(js_object *self);
Py_ssize_t js_object_length(js_object *self);
PyObject *js_object_dir(js_object *self);
PyObject *js_object_repr(js_object *self);
PyObject* js_object_richcompare(js_object* self, PyObject* other, int operation);


#endif
