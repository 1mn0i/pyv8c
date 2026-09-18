#ifndef PYCLASS_HANDLERS_INTERNAL_H
#define PYCLASS_HANDLERS_INTERNAL_H

#include "pyclass.h"
#include "pyv8.h"

template <class Info>
inline PyObject*
get_handler_self(const Info& info, bool cross_origin)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    v8::Local<v8::Object> js_self = v8_compat::callback_holder(info);
    bool is_global = cross_origin
        ? js_self->Equals(context, context->Global()).FromMaybe(false)
        : js_self == context->Global();
    if (is_global)
        js_self = v8_compat::global_template_object(context);

    if (js_self->InternalFieldCount() != OBJECT_INTERNAL_FIELDS)
        return Py_None;
    v8::Local<v8::Data> magic = js_self->GetInternalField(PYTHON_MAGIC_FIELD);
    if (magic != runtime_manager.find_isolate(isolate)->v8_string(V8StringId::PythonObjectMagic))
        return Py_None;
    return IsolateState::python_object(js_self);
}

template <class T>
inline PyObject* get_self(const v8::PropertyCallbackInfo<T>& info) {
    return get_handler_self(info, false);
}

template <class T>
inline PyObject* get_self(const v8::FunctionCallbackInfo<T>& info) {
    return get_handler_self(info, false);
}

template <class T>
inline PyObject* get_self_cross_origin(const v8::PropertyCallbackInfo<T>& info) {
    return get_handler_self(info, true);
}

template <class T>
inline PyObject* get_self_cross_origin(const v8::FunctionCallbackInfo<T>& info) {
    return get_handler_self(info, true);
}

v8::Intercepted property_getter_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Value>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    const char* method_name,
    v8::Local<v8::Name> js_name = v8::Local<v8::Name>(),
    bool report_declined = true);

v8::Intercepted property_setter_handler(
    PyObject* key,
    v8::Local<v8::Value> js_value,
    const v8::PropertyCallbackInfo<void>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    const char* method_name,
    bool report_declined = true);

v8::Intercepted property_deleter_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Boolean>& info,
    PyObject* self,
    const char* method_name);

v8::Intercepted property_query_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Integer>& info,
    PyObject* self,
    const char* method_name);

void named_enumerator_handler(
    const v8::PropertyCallbackInfo<v8::Array>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    const char* method_name);

void indexed_enumerator_handler(
    const v8::PropertyCallbackInfo<v8::Array>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    const char* method_name);

#endif
