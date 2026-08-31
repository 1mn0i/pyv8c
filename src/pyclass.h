#ifndef CLASS_TEMPLATE_H
#define CLASS_TEMPLATE_H

#include <Python.h>
#include "context.h"

typedef struct {
    PyObject_HEAD
    IsolateState* isolate_state;
    PyObject *cls;
    PyObject *cls_name;
    PyObject *call_method;
    PyObject *operation_callback_data;
    PyObject *constructor_adapter;
    PyObject *parent;
    int constructor_behavior;
    int unconstructable;
    int has_constructor;
    int to_obj;
    v8::Persistent<v8::FunctionTemplate> templ;
} py_class;

int py_class_type_init();
extern PyTypeObject* py_class_type;

PyObject* py_class_new(PyObject* cls, context_c *py_ctx);
void py_class_dealloc(py_class* self);
PyObject* py_class_to_template(PyObject* cls, context_c *py_ctx);
v8::Local<v8::Function> py_class_get_constructor(v8::Local<v8::Context> context, py_class* self, py_class* parent);
v8::Local<v8::Object> py_class_create_js_object(py_class *self, PyObject *py_object, v8::Local<v8::Context> context);
bool py_class_init_js_object(v8::Local<v8::Object> js_object, PyObject *py_object, v8::Local<v8::Context> context);
void delete_constructor(py_class* self, v8::Local<v8::Context> context);
enum PythonObjectInternalField {
    PYTHON_MAGIC_FIELD = 0,
    PYTHON_OBJECT_FIELD = 1,
    PYTHON_EXCEPTION_TYPE_FIELD = 2,
    PYTHON_TRACEBACK_FIELD = 3,
    PYTHON_OWNER_FIELD = 4,
    OBJECT_INTERNAL_FIELDS = 5,
};

void py_class_construct_callback(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_class_method_callback(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_class_cross_origin_method_callback(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_class_noop_call(const v8::FunctionCallbackInfo<v8::Value>& info);

// Handlers
v8::Intercepted properties_named_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info);

v8::Intercepted named_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted named_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted named_query(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted named_deleter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Boolean>& info);
void named_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

v8::Intercepted indexed_getter(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted indexed_setter(uint32_t index, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted indexed_query(uint32_t index, const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted indexed_deleter(uint32_t index, const v8::PropertyCallbackInfo<v8::Boolean>& info);
void indexed_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

void py_class_property_getter(v8::Local<v8::Name> js_name, const v8::PropertyCallbackInfo<v8::Value>& info);
void py_class_property_setter(v8::Local<v8::Name> js_name, v8::Local<v8::Value> js_value, const v8::PropertyCallbackInfo<void>& info);

void py_attribute_for_get(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_attribute_for_instance_set(const v8::FunctionCallbackInfo<v8::Value>& info);
void py_attribute_for_property_set(const v8::FunctionCallbackInfo<v8::Value>& info);

#endif
