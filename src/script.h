#include <Python.h>
#include <v8.h>

#include "pyv8.h"

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    Persistent<UnboundScript> script;
    PyObject* py_isolate;
    PyObject *source;
    PyObject *script_name;
} script_c;

extern PyTypeObject* script_type;

int script_type_init();
PyObject *script_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void script_dealloc(script_c *self);

MaybeLocal<UnboundScript> script_compile(Local<Context> context, PyObject *source, PyObject *filename);
