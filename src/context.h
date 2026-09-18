#ifndef CONTEXT_H
#define CONTEXT_H


#include <Python.h>
#include <v8.h>
#include <unordered_map>
#include <unordered_set>

class IsolateState;
struct context_c;

struct ContextResources {
    std::unordered_map<PyObject*, int (*)(PyObject*)> handles;
    std::unordered_set<context_c*> dependents;
    std::unordered_set<context_c*> dependencies;
};

typedef struct context_c
{
    PyObject_HEAD

    IsolateState* isolate_state;
    PyObject* py_isolate;
    v8::Persistent<v8::Context> js_context;
    PyObject* js_object_cache;
    PyObject* class_template_cache;
    PyObject* function_template_cache;
    PyObject* uncaught_handler;
    PyObject* rejection_handler;
    PyObject* dependencies;
    ContextResources* resources;
    bool closing;
    PyObject* class_configs;
    // Optional callable used to materialise lazily declared interface names.
    // Called with an interface name, returns the Python class or None.
    PyObject* lazy_factory;
    PyObject* resolving_classes;
    PyObject* lazy_names;
    PyObject* py_global;
    bool has_done;
    double timeout;
    bool trace;
    bool traceable;
    // Opt-in lazy object arguments for hosts which own Web API conversion.
    bool preserve_arguments;
    unsigned int trace_depth;

    PyObject* trace_callback;
    PyObject* trace_context_type;
    PyObject* trace_event_names;
} context_c;

bool context_is_active(context_c* self);
bool context_check_callback(v8::Local<v8::Context> context);
void context_finish_close(context_c* self);
void context_register_handle(context_c* self, PyObject* handle, int (*clear)(PyObject*));
void context_unregister_handle(context_c* self, PyObject* handle);
bool context_retain_dependency(v8::Local<v8::Context> target, PyObject* source);

inline bool context_trace_enabled(context_c* context) {
    return context != nullptr && context->trace && context->trace_callback != nullptr && context->trace_depth == 0;
}

extern PyTypeObject* context_type;
int context_type_init();
PyObject* context_get_trace(context_c* self, void* closure);
int context_set_trace(context_c* self, PyObject* value, void* closure);

void context_dealloc(context_c* self);
PyObject* context_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);
PyObject* context_run(context_c* self, PyObject* args, PyObject* kwargs);
PyObject* context_expose(context_c* self, PyObject* args, PyObject* kwargs);
PyObject* context_expose_module(context_c* self, PyObject* module);
PyObject* context_js_obj(context_c* self, PyObject* py_object);
PyObject* context_declare_lazy(context_c* self, PyObject* args, PyObject* kwargs);
PyObject* context_update_configs(context_c* self, PyObject* mapping);
PyObject* context_gc(context_c* self);
PyObject* context_close(context_c* self, PyObject* ignored);
PyObject* context_enqueue_microtask(context_c* self, PyObject* function);
PyObject* context_set_error_hooks(context_c* self, PyObject* args, PyObject* kwargs);

// Embedder data slots
#define CONTEXT_OBJECT_SLOT 1
#define OBJECT_PROTOTYPE_SLOT 2
#define ERROR_PROTOTYPE_SLOT 3
#define CTX_TYPE 4

v8::Local<v8::Object> context_get_cached_jsobject(v8::Local<v8::Context> context, PyObject* py_object);
void context_set_cached_jsobject(v8::Local<v8::Context> context, PyObject* py_object, v8::Local<v8::Object> object);

PyObject* context_get_global(context_c* self, void* shit);

PyObject* context_get_timeout(context_c* self, void* shit);
int context_set_timeout(context_c* self, PyObject* value, void* shit);

PyObject* context_getattro(context_c* self, PyObject* name);
PyObject* context_getitem(context_c* self, PyObject* name);
int context_setattro(context_c* self, PyObject* name, PyObject* value);
int context_setitem(context_c* self, PyObject* name, PyObject* value);


#endif
