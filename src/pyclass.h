#ifndef CLASS_TEMPLATE_H
#define CLASS_TEMPLATE_H

#include <Python.h>
#include "context.h"

using namespace v8;

typedef struct {
    PyObject_HEAD
    PyObject *cls;
    PyObject *cls_name;
    PyObject *parent;
    int has_constructor;
    int to_obj;
    Persistent<FunctionTemplate> templ;
} py_class;

int py_class_type_init();
extern PyTypeObject* py_class_type;

PyObject* py_class_new(PyObject* cls, context_c *py_ctx);
void py_class_dealloc(py_class* self);
PyObject* py_class_to_template(PyObject* cls, context_c *py_ctx);
Local<Function> py_class_get_constructor(Local<Context> context, py_class* self, py_class* parent);
Local<Object> py_class_create_js_object(py_class *self, PyObject *py_object, Local<Context> context);
bool py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context);
void delete_constructor(py_class* self, Local<Context> context);
enum PythonObjectInternalField {
    PYTHON_MAGIC_FIELD = 0,
    PYTHON_OBJECT_FIELD = 1,
    PYTHON_EXCEPTION_TYPE_FIELD = 2,
    PYTHON_TRACEBACK_FIELD = 3,
    PYTHON_OWNER_FIELD = 4,
    OBJECT_INTERNAL_FIELDS = 5,
};

void py_class_construct_callback(const FunctionCallbackInfo<Value>& info);
void py_class_method_callback(const FunctionCallbackInfo<Value>& info);
void py_class_cross_origin_method_callback(const FunctionCallbackInfo<Value>& info);

// Handlers 
Intercepted properties_named_getter(Local<Name> name, const PropertyCallbackInfo<Value>& info);

Intercepted named_getter(Local<Name> name, const PropertyCallbackInfo<Value>& info);
Intercepted named_setter(Local<Name> name, Local<Value> value, const PropertyCallbackInfo<void>& info);
Intercepted named_query(Local<Name> name, const PropertyCallbackInfo<Integer>& info);
Intercepted named_deleter(Local<Name> name, const PropertyCallbackInfo<Boolean>& info);
void named_enumerator(const PropertyCallbackInfo<Array>& info);

Intercepted indexed_getter(uint32_t index, const PropertyCallbackInfo<Value>& info);
Intercepted indexed_setter(uint32_t index, Local<Value> value, const PropertyCallbackInfo<void>& info);
Intercepted indexed_query(uint32_t index, const PropertyCallbackInfo<Integer>& info);
Intercepted indexed_deleter(uint32_t index, const PropertyCallbackInfo<Boolean>& info);
void indexed_enumerator(const PropertyCallbackInfo<Array>& info);

void py_class_property_getter(Local<Name> js_name, const PropertyCallbackInfo<Value>& info);
void py_class_property_setter(Local<Name> js_name, Local<Value> js_value, const PropertyCallbackInfo<void>& info);

void py_attribute_for_get(const FunctionCallbackInfo<Value>& info);
void py_attribute_for_instance_set(const FunctionCallbackInfo<Value>& info);
void py_attribute_for_property_set(const FunctionCallbackInfo<Value>& info);

// cross origin access handlers
bool cross_origin_access_check(v8::Local<v8::Context> accessing_context, v8::Local<v8::Object> accessed_object, v8::Local<v8::Value> unused_data);

Intercepted cross_origin_named_getter(Local<Name> name, const PropertyCallbackInfo<Value>& info);
Intercepted cross_origin_named_setter(Local<Name> name, Local<Value> value, const PropertyCallbackInfo<void>& info);
Intercepted cross_origin_named_query(Local<Name> name, const PropertyCallbackInfo<Integer>& info);
Intercepted cross_origin_named_deleter(Local<Name> name, const PropertyCallbackInfo<Boolean>& info);
void cross_origin_named_enumerator(const PropertyCallbackInfo<Array>& info);

Intercepted cross_origin_indexed_getter(uint32_t index, const PropertyCallbackInfo<Value>& info);
Intercepted cross_origin_indexed_setter(uint32_t index, Local<Value> value, const PropertyCallbackInfo<void>& info);
Intercepted cross_origin_indexed_query(uint32_t index, const PropertyCallbackInfo<Integer>& info);
Intercepted cross_origin_indexed_deleter(uint32_t index, const PropertyCallbackInfo<Boolean>& info);
void cross_origin_indexed_enumerator(const PropertyCallbackInfo<Array>& info);
#endif
