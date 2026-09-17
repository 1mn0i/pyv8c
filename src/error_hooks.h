#ifndef PYV8_ERROR_HOOKS_H
#define PYV8_ERROR_HOOKS_H

#include <Python.h>

class IsolateState;

// Route the failures V8 handles by itself back into Python.
//
// An embedder sees every exception it caused: the call it made returns as a
// Python error.  What it cannot see is what V8 runs on its own -- microtask
// jobs and promise reactions never return to a Python frame -- and an unhandled
// rejection is not an exception at all.  V8 reports both through isolate-wide
// callbacks instead, and using those is also the cheap way to do it: they are
// installed once, where wrapping every enqueued function in JavaScript would
// cost on every call.
//
// ``on_uncaught`` is called as ``(message, source, line, column, error)`` with
// the exception V8 could not deliver, and ``on_rejection`` as
// ``(event, promise, reason)``, where ``event`` is ``"unhandledrejection"`` or
// ``"rejectionhandled"``.  Either may be ``None`` to leave that report alone.
//
// Both run on the thread executing the isolate, with the GIL held -- V8 only
// calls them while a realm thread is inside JavaScript -- and the pending
// Python error state is restored around the call, so a reporter cannot disturb
// the JavaScript failure it is reporting.
bool error_hooks_install(IsolateState* state, PyObject* on_uncaught, PyObject* on_rejection);

// Drop the reporters and the callbacks they were installed with.  Safe to call
// on an isolate that never installed them.
void error_hooks_uninstall(IsolateState* state);

#endif
