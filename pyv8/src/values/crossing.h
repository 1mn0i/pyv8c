#ifndef PYV8_CROSSING_H
#define PYV8_CROSSING_H

#include "bridge_base.h"
#include <Python.h>
#include <v8.h>
#include "js_exception.h"

// One crossing of the language boundary, in either direction, and what the host's own
// failure does there.  A JavaScript exception becomes a ``pyv8.JSException`` (see
// js_exception.h); a Python failure the error family does not contain (see
// error_family.h) is kept from the realm: the crossing is terminated, the failure is
// held for the Python boundary that began the execution, and no realm ever hears of
// it.
//
// The propagation macros below are the boundary every bridge callback uses.  Their
// names and semantics are a stable contract across the interfaces in src/interfaces;
// callers include this boundary contract explicitly.

void py_throw_js(v8::Local<v8::Value> js_exc, v8::Local<v8::Message> js_message);

// Whether the crossing this layer ended carried the host's own failure: when it did,
// that failure is raised instead of the bare termination V8 reports in its place.
bool resume_aborted_crossing();

//: One ``JS_TRY`` per scope: the macros below answer through the ``tc`` it declares.
#define JS_TRY v8::TryCatch tc(isolate);
#define PY_PROPAGATE_JS_RET(retval)                    \
    do {                                               \
        if (resume_aborted_crossing())                 \
        {                                              \
            return retval;                             \
        }                                              \
        if (tc.HasCaught())                            \
        {                                              \
            if (tc.CanContinue())                      \
            {                                          \
                py_throw_js(tc.Exception(), tc.Message()); \
            }                                          \
            else                                       \
            {                                          \
                PyErr_SetNone((PyObject *) js_terminated_type);         \
            }                                          \
            return retval;                             \
        }                                              \
    } while (0)
#define PY_PROPAGATE_JS PY_PROPAGATE_JS_RET(NULL)

void js_throw_py();
#define JS_PROPAGATE_PY(value) \
    do {                       \
        if ((value) == NULL)   \
        {                      \
            js_throw_py();     \
            return;            \
        }                      \
    } while (0)

#endif
