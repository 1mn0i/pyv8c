#ifndef JS_PROMISE_H
#define JS_PROMISE_H

#include <Python.h>
#include <v8.h>
#include "jsobject.h"
#include "handle_resource.h"
#include "v8_handle_slot.h"

typedef struct {
    js_object base;
} js_promise;

// A promise's resolver, held from Python: ``JSPromise`` builds one, settles it at once and
// returns the promise, while this is the half that creates the promise now and settles it later.
//
// The resolver keeps its Context alive, so a handle may outlive the call that made it; once the
// realm closes, every method answers a plain error naming the realm.
typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    V8HandleSlot<v8::Promise::Resolver> resolver;
    PyObject* py_context;
    PyObject* py_isolate;
    HandleRegistration registration;
} js_promise_resolver;

extern PyTypeObject* js_promise_type;
int js_promise_type_init();
void js_promise_dealloc(js_promise *self);
PyObject* js_promise_repr(js_promise* self);
PyObject* js_promise_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);

extern PyTypeObject* js_promise_resolver_type;
int js_promise_resolver_type_init();
void js_promise_resolver_dealloc(js_promise_resolver *self);
int js_promise_resolver_clear(js_promise_resolver* self);
PyObject* js_promise_resolver_repr(js_promise_resolver* self);
PyObject* js_promise_resolver_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);
PyObject* js_promise_resolver_promise(js_promise_resolver* self, PyObject* unused);
PyObject* js_promise_resolver_resolve(js_promise_resolver* self, PyObject* value);
PyObject* js_promise_resolver_reject(js_promise_resolver* self, PyObject* value);

#endif
