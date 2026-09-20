#ifndef CONTEXT_H
#define CONTEXT_H


#include <Python.h>
#include <v8.h>
#include <unordered_map>
#include <unordered_set>
#include <memory>

#include "v8_handle_slot.h"

class IsolateState;
struct context_c;
enum class ContextState { Active, Closing, Closed };
struct ContextLifetime {
    context_c* owner;

    // The pair table: a Python host object -> the JavaScript object standing for
    // it in this realm.  The table owns neither side; an entry goes when its
    // owner is released.  It lives here, not on the context, so a handle that
    // outlives the context reads an empty table.
    std::unordered_map<PyObject*, PythonObjectOwner*> js_object_cache;

    explicit ContextLifetime(context_c* owner) : owner(owner) {}
};

struct ContextResources {
    std::shared_ptr<ContextLifetime> lifetime;
    std::unordered_set<PythonObjectOwner*> python_owners;
    std::unordered_set<void*> callbacks;
    std::unordered_map<PyObject*, int (*)(PyObject*)> handles;
    std::unordered_set<context_c*> dependents;
    std::unordered_set<context_c*> dependencies;
    // Where each dependency sits in `owner->dependencies`, so removing one is a
    // swap instead of a scan of the list.
    std::unordered_map<context_c*, Py_ssize_t> dependency_slots;
};

struct PythonObjectOwner;

// Every Python reference a realm owns.  The struct, the traversal and the close
// walk all read this one list, so a field cannot be added without being visited.
#define CONTEXT_PY_FIELDS(X) \
    X(py_isolate)            \
    X(class_template_cache)  \
    X(function_template_cache) \
    X(callback_targets)      \
    X(class_configs)         \
    X(lazy_factory)          \
    X(resolving_classes)     \
    X(lazy_names)            \
    X(py_global)             \
    X(trace_callback)        \
    X(trace_context_type)    \
    X(trace_event_names)     \
    X(uncaught_handler)      \
    X(rejection_handler)     \
    X(dependencies)

typedef struct context_c
{
    PyObject_HEAD

    IsolateState* isolate_state;
    PyObject* py_isolate;
    V8HandleSlot<v8::Context> js_context;
    PyObject* class_template_cache;
    PyObject* function_template_cache;
    PyObject* callback_targets;
    PyObject* uncaught_handler;
    PyObject* rejection_handler;
    PyObject* dependencies;
    ContextResources* resources;
    ContextState state;
    context_c* next_pending_close;
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
    unsigned int trace_depth;

    PyObject* trace_callback;
    PyObject* trace_context_type;
    PyObject* trace_event_names;
} context_c;

// A realm's template caches own their entries for as long as the realm lives; a
// lookup hands the caller a reference of its own, which it releases as soon as
// it is done.  Returning the cached reference itself left callers unsure
// whether releasing it was allowed.
inline PyObject* owned_template(PyObject* templ)
{
    Py_XINCREF(templ);
    return templ;
}

bool context_is_active(context_c* self);
context_c* context_from_v8(v8::Local<v8::Context> context);
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

PyObject* context_get_global(context_c* self, void* ignored);

PyObject* context_get_timeout(context_c* self, void* ignored);
int context_set_timeout(context_c* self, PyObject* value, void* ignored);

PyObject* context_getattro(context_c* self, PyObject* name);
PyObject* context_getitem(context_c* self, PyObject* name);
int context_setattro(context_c* self, PyObject* name, PyObject* value);
int context_setitem(context_c* self, PyObject* name, PyObject* value);


#endif
