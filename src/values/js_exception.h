#ifndef PYV8_JS_EXCEPTION_H
#define PYV8_JS_EXCEPTION_H

#include "bridge_base.h"
#include <Python.h>
#include <v8.h>
#include "context.h"
#include "v8_handle_slot.h"

// The Python face of a JavaScript exception: the ``pyv8.JSException`` type a host
// catches, the state that keeps the thrown value and the message alive for its
// getters, and the message text both directions read.  The crossing itself -- what
// throws in either direction and what terminates -- lives in crossing.h.

struct JsExceptionState
{
    PyObject_HEAD
    IsolateState* isolate_state;
    V8HandleSlot<v8::Value> exception;
    V8HandleSlot<v8::Message> message;
    PyObject* py_context;
    PyObject* py_isolate;
};

extern PyTypeObject* js_exception_type;
extern PyTypeObject* js_terminated_type;
int js_exception_type_init();
int js_terminated_type_init();

PyObject* js_exception_new(v8::Local<v8::Value> exception, v8::Local<v8::Message> message);
JsExceptionState* js_exception_get_state(PyObject* exception);

// The message a Python exception becomes in JavaScript, as a new reference: its
// message, not its string form, so a class whose __str__ adds its name (a
// DOMException) does not arrive with the name twice.
PyObject* pyexception_message(PyObject* value);

#endif
