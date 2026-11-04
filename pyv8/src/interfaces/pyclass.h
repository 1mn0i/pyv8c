#ifndef CLASS_TEMPLATE_H
#define CLASS_TEMPLATE_H

#include <Python.h>
#include "context.h"
#include "handle_resource.h"
#include "v8_handle_slot.h"

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    PyObject *cls;
    PyObject *idl_spec;
    PyObject *cls_name;
    PyObject *operation_callback_data;
    PyObject *constructor_adapter;
    PyObject *parent;
    int constructor_behavior;
    int unconstructable;
    int has_constructor;
    int to_obj;
    // 1 when a realm is shown this class as a JavaScript error; decided with its template.
    int error_class;
    // 0 until an iteration source's iterator prototype is finished, 1 afterwards.
    int iterator_prototype;
    // 1 for a legacy factory (Option/Image/Audio): its ``prototype`` is the interface's own
    // prototype object, as Blink installs them, not a fresh object of its own.
    int shares_parent_prototype;
    V8HandleSlot<v8::FunctionTemplate> templ;
    // The iterator an interface with an iteration source hands out; empty otherwise.
    V8HandleSlot<v8::FunctionTemplate> iterator_templ;
} py_class;

// Only IDL interface instances need cross-realm wrapper identity. Namespaces,
// error wrappers, and plain Python classes stay owned by the realm that built them.
inline bool py_class_shares_wrapper(const py_class* self)
{
    return self->idl_spec != nullptr && !self->to_obj && !self->error_class;
}

int py_class_type_init();
extern PyTypeObject* py_class_type;

PyObject* py_class_new(PyObject* cls, context_c *py_ctx, bool shared = false);
// Release the persistent V8 handles a shared template holds, so the isolate can close
// even while the Python object is still referenced by realm registries.
void py_class_release_handles(PyObject* templ);
void py_class_dealloc(py_class* self);
// Returns a reference of its own; the realm's cache keeps the template alive.
PyObject* py_class_to_template(PyObject* cls, context_c *py_ctx);
// A function for an interface or a callback interface, an object for a namespace (``CSS``).
v8::Local<v8::Value> py_class_get_constructor(v8::Local<v8::Context> context, py_class* self, py_class* parent);
v8::Local<v8::Object> py_class_create_js_object(py_class *self, PyObject *py_object, v8::Local<v8::Context> context);
// Publishes the object/instance pair and stamps the marker the conversion reads back.
// Only IDL host objects share one wrapper across realms; a plain Python object owns
// one wrapper per realm.
bool py_class_init_js_object(v8::Local<v8::Object> js_object, PyObject *py_object,
                             v8::Local<v8::Context> context,
                             bool share_wrapper = false);
// The interceptor entry points are declared in pyclass_handlers.h, beside the
// handlers they look up, because they are reached only from there: a class hands
// them to V8 when it installs its templates.
void delete_constructor(py_class* self, v8::Local<v8::Context> context);

void py_class_construct_callback(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_class_method_callback(const v8::FunctionCallbackInfo<v8::Value>& info);

void py_attribute_for_get(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_attribute_for_instance_set(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_attribute_for_property_set(const v8::FunctionCallbackInfo<v8::Value>& info);

#endif
