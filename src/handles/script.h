#include <Python.h>
#include <v8.h>

#include "pyv8.h"
#include "handle_resource.h"

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    v8::Persistent<v8::UnboundScript> script;
    PyObject* py_isolate;
    PyObject* py_context;
    HandleRegistration registration;
    PyObject *source;
    PyObject *script_name;
} script_c;

extern PyTypeObject* script_type;

int script_type_init();
PyObject *script_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
void script_dealloc(script_c *self);

v8::MaybeLocal<v8::UnboundScript> script_compile(v8::Local<v8::Context> context, PyObject *source, PyObject *filename);
