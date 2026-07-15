#ifndef PYCLASS_HANDLERS_INTERNAL_H
#define PYCLASS_HANDLERS_INTERNAL_H

#include "pyclass.h"
#include "pyv8.h"

template <class Info>
inline PyObject*
get_handler_self(const Info& info, bool cross_origin)
{
    Isolate* isolate = info.GetIsolate();
    Local<Context> context = isolate->GetCurrentContext();
    Local<Object> js_self = info.Holder();
    bool is_global = cross_origin
        ? js_self->Equals(context, context->Global()).FromMaybe(false)
        : js_self == context->Global();
    if (is_global) {
        Local<Value> prototype = js_self->GetPrototype();
        if (!prototype->IsObject())
            return Py_None;
        js_self = prototype.As<Object>();
    }

    if (js_self->InternalFieldCount() != OBJECT_INTERNAL_FIELDS)
        return Py_None;
    Local<Data> magic = js_self->GetInternalField(PYTHON_MAGIC_FIELD);
    if (magic != runtime_manager.find_isolate(isolate)->v8_string(V8StringId::PythonObjectMagic))
        return Py_None;
    return (PyObject*)js_self->GetInternalField(PYTHON_OBJECT_FIELD)
        .template As<External>()->Value();
}

template <class T>
inline PyObject* get_self(const PropertyCallbackInfo<T>& info) {
    return get_handler_self(info, false);
}

template <class T>
inline PyObject* get_self(const FunctionCallbackInfo<T>& info) {
    return get_handler_self(info, false);
}

template <class T>
inline PyObject* get_self_cross_origin(const PropertyCallbackInfo<T>& info) {
    return get_handler_self(info, true);
}

template <class T>
inline PyObject* get_self_cross_origin(const FunctionCallbackInfo<T>& info) {
    return get_handler_self(info, true);
}

Intercepted property_getter_handler(
    PyObject* key,
    const PropertyCallbackInfo<Value>& info,
    Local<Context> context,
    PyObject* self,
    const char* method_name);

Intercepted property_setter_handler(
    PyObject* key,
    Local<Value> js_value,
    const PropertyCallbackInfo<void>& info,
    Local<Context> context,
    PyObject* self,
    const char* method_name);

Intercepted property_deleter_handler(
    PyObject* key,
    const PropertyCallbackInfo<Boolean>& info,
    PyObject* self,
    const char* method_name);

Intercepted property_query_handler(
    PyObject* key,
    const PropertyCallbackInfo<Integer>& info,
    PyObject* self,
    const char* method_name);

void named_enumerator_handler(
    const PropertyCallbackInfo<Array>& info,
    Local<Context> context,
    PyObject* self,
    const char* method_name);

void indexed_enumerator_handler(
    const PropertyCallbackInfo<Array>& info,
    Local<Context> context,
    PyObject* self,
    const char* method_name);

#endif
