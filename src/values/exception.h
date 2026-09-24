#ifndef EXCEPTION_H
#define EXCEPTION_H

#include <Python.h>
#include <v8.h>
#include "context.h"
#include "v8_handle_slot.h"

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
PyObject* js_exception_get_value(PyObject* self, void* args);

// The message a Python exception becomes in JavaScript, as a new reference: its
// message, not its string form, so a class whose __str__ adds its name (a
// DOMException) does not arrive with the name twice.
PyObject* pyexception_message(PyObject* value);

void py_throw_js(v8::Local<v8::Value> js_exc, v8::Local<v8::Message> js_message);
#define JS_TRY v8::TryCatch tc(isolate);
#define PY_PROPAGATE_JS_RET(retval)                    \
    if (tc.HasCaught())                                \
    {                                                  \
        if (tc.CanContinue())                          \
        {                                              \
            py_throw_js(tc.Exception(), tc.Message()); \
        }                                              \
        else                                           \
        {                                              \
            PyErr_SetNone((PyObject *) js_terminated_type);         \
        }                                              \
        return retval;                                 \
    }
#define PY_PROPAGATE_JS PY_PROPAGATE_JS_RET(NULL)
#define PY_PROPAGATE_JS_ PY_PROPAGATE_JS_RET(-1)

void js_throw_py();
#define JS_PROPAGATE_PY(value) \
    if (value == NULL)         \
    {                          \
        js_throw_py();         \
        return;                \
    }
#define JS_PROPAGATE_PY_(value) \
    if (value < 0)              \
    {                           \
        js_throw_py();          \
        return;                 \
    }

#endif
