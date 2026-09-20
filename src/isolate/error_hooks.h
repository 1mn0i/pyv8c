#ifndef PYV8_ERROR_HOOKS_H
#define PYV8_ERROR_HOOKS_H

#include <Python.h>

class IsolateState;
struct context_c;

// Route the failures V8 handles by itself back into Python: an exception from a
// microtask job or promise reaction, and a rejection.  Both run on the isolate's
// thread with the GIL held; either may be None.
//   on_uncaught(message, source, line, column, error)
//   on_rejection(event, identity, promise, reason)
bool error_hooks_install(context_c* context, PyObject* on_uncaught, PyObject* on_rejection);

// Remove the native listeners. Python reporters are released by Context.close.
// Safe to call
// on an isolate that never installed them.
void error_hooks_uninstall(IsolateState* state);

#endif
