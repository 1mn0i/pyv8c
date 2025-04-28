#ifndef CONTEXT_H
#define CONTEXT_H


#include <Python.h>
#include <v8.h>


using namespace v8;

typedef struct
{
    PyObject_HEAD

    Persistent<Context> js_context;
    PyObject* js_object_cache;
    PyObject* py_global;
    PyObject* scripts;
    bool has_debugger;
    bool has_done;
    double timeout;
    bool hook;
} context_c;

extern PyTypeObject* context_type;
int context_type_init();

bool context_setup_timeout(Local<Context> context);
bool context_cleanup_timeout(Local<Context> context);

void context_dealloc(context_c* self);
PyObject* context_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);
PyObject* context_run(context_c* self, PyObject* args, PyObject* kwargs);
PyObject* context_expose(context_c* self, PyObject* args, PyObject* kwargs);
PyObject* context_expose_module(context_c* self, PyObject* module);
PyObject* context_js_obj(context_c* self, PyObject* py_object);
PyObject* context_gc(context_c* self);

// Embedder data slots
#define CONTEXT_OBJECT_SLOT 1
#define OBJECT_PROTOTYPE_SLOT 2
#define ERROR_PROTOTYPE_SLOT 3
#define CTX_TYPE 4

Local<Object> context_get_cached_jsobject(Local<Context> context, PyObject* py_object);
void context_set_cached_jsobject(Local<Context> context, PyObject* py_object, Local<Object> object);

PyObject* context_get_global(context_c* self, void* shit);

PyObject* context_get_timeout(context_c* self, void* shit);
int* context_set_timeout(context_c* self, PyObject* value, void* shit);

PyObject* context_getattro(context_c* self, PyObject* name);
PyObject* context_getitem(context_c* self, PyObject* name);
int context_setattro(context_c* self, PyObject* name, PyObject* value);
int context_setitem(context_c* self, PyObject* name, PyObject* value);


#endif
