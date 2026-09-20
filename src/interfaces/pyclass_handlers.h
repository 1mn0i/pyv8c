#ifndef PYCLASS_HANDLERS_H
#define PYCLASS_HANDLERS_H

#include "pyclass.h"
#include "pyv8.h"

#define SETUP                                                          \
    v8::Isolate* isolate = info.GetIsolate();                          \
    v8::HandleScope hs(isolate);                                       \
    v8::Local<v8::Context> context = isolate->GetCurrentContext();     \
    v8::Context::Scope cs(context);

#define Info(T) const v8::PropertyCallbackInfo<T> &info

// Does a real property provide ``name`` on the holder or its prototypes?  The
// walk skips interceptors and only asks the engine what it stores, so it never
// runs JavaScript and is safe to use while reporting a probe.
template <class Info>
inline bool real_property_exists(const v8::PropertyCallbackInfo<Info>& info,
                                 v8::Local<v8::Name> js_name, v8::Local<v8::Context> context) {
    if (js_name.IsEmpty()) return false;
    v8::Local<v8::Object> object = v8_compat::callback_holder(info);
    while (!object.IsEmpty()) {
        if (object->HasRealNamedProperty(context, js_name).FromMaybe(false)) return true;
        v8::Local<v8::Value> prototype = object->GetPrototype();
        object = prototype->IsObject() ? prototype.As<v8::Object>() : v8::Local<v8::Object>();
    }
    return false;
}

// Does the holder itself store a real property for ``name``?  A descriptor probe
// answers about own properties, which is what ``Object.getOwnPropertyDescriptor``
// and ``hasOwnProperty`` ask for, while ``in`` walks the whole prototype chain.
template <class Info>
inline bool own_real_property_exists(const v8::PropertyCallbackInfo<Info>& info,
                                     v8::Local<v8::Name> js_name, v8::Local<v8::Context> context) {
    if (js_name.IsEmpty()) return false;
    return info.Holder()->HasRealNamedProperty(context, js_name).FromMaybe(false);
}

// The entry points V8 calls on a host object whose class installed interceptors,
// and the handlers they reach: a class hands these to SetHandler when it builds
// its templates, so they live beside the lookup that turns one of them into a
// Python callable.
v8::Intercepted properties_named_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted properties_named_query(v8::Local<v8::Name>, const v8::PropertyCallbackInfo<v8::Integer>&);
v8::Intercepted properties_named_deleter(v8::Local<v8::Name>, const v8::PropertyCallbackInfo<v8::Boolean>&);
void properties_named_enumerator(const v8::PropertyCallbackInfo<v8::Array>&);
void properties_delete_constructor(v8::Local<v8::Object>, v8::Local<v8::Context>);
v8::Intercepted properties_indexed_getter(uint32_t, const v8::PropertyCallbackInfo<v8::Value>&);
v8::Intercepted properties_indexed_query(uint32_t, const v8::PropertyCallbackInfo<v8::Integer>&);
v8::Intercepted properties_indexed_deleter(uint32_t, const v8::PropertyCallbackInfo<v8::Boolean>&);
v8::Intercepted properties_indexed_descriptor(uint32_t, const v8::PropertyCallbackInfo<v8::Value>&);
v8::Intercepted properties_indexed_definer(uint32_t, const v8::PropertyDescriptor&, const v8::PropertyCallbackInfo<v8::Boolean>&);
v8::Intercepted properties_named_descriptor(v8::Local<v8::Name>, const v8::PropertyCallbackInfo<v8::Value>&);
v8::Intercepted properties_named_definer(v8::Local<v8::Name>, const v8::PropertyDescriptor&, const v8::PropertyCallbackInfo<v8::Boolean>&);

v8::Intercepted named_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted named_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted named_query(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted named_deleter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Boolean>& info);
void named_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);
v8::Intercepted named_definer(v8::Local<v8::Name> name, const v8::PropertyDescriptor& desc, const v8::PropertyCallbackInfo<v8::Boolean>& info);
v8::Intercepted named_descriptor(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info);

v8::Intercepted indexed_getter(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted indexed_setter(uint32_t index, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted indexed_query(uint32_t index, const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted indexed_deleter(uint32_t index, const v8::PropertyCallbackInfo<v8::Boolean>& info);
void indexed_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);
v8::Intercepted indexed_definer(uint32_t index, const v8::PropertyDescriptor& desc, const v8::PropertyCallbackInfo<v8::Boolean>& info);
v8::Intercepted indexed_descriptor(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info);

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
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (isolate_state == nullptr)
        return Py_None;
    v8::Local<v8::Data> magic = js_self->GetInternalField(PYTHON_MAGIC_FIELD);
    if (magic != isolate_state->v8_string(V8StringId::PythonObjectMagic))
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

// Which of an interface's declared handlers a lookup wants.  Generated from
// handler_table.def, which is also where the declaration names and the spec fields
// come from, so the three cannot drift apart.
enum class HandlerId {
#define PYV8_HANDLER(id, declaration, field) id,
#define PYV8_PROPERTY_NAME_HANDLER(id, declaration) id,
#include "handler_table.def"
#undef PYV8_HANDLER
#undef PYV8_PROPERTY_NAME_HANDLER
    Count,
};

// The handler *receiver* declares for *which*, as a new reference, or null when it
// declares none -- which is how an interface declines an operation.  A Python
// error is set only when the lookup itself failed.
PyObject* py_class_handler(PyObject* receiver, HandlerId which, context_c* context);

// argv[0] holds the receiver.  A declared handler takes it as its first parameter;
// a bound method already carries one.
PyObject* call_handler(PyObject* handler, PyObject* argv[], size_t count);

// Machinery the entry points of the other translation unit reach as well: one
// step of a read, the descriptor an own-property probe builds from it, and the
// probe a define asks for.
v8::Intercepted property_getter_handler_impl(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Value>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which);

v8::Intercepted property_descriptor(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Value>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which,
    bool enumerable);

bool property_query_value(PyObject* key, PyObject* self, HandlerId which,
                          long* attributes);

v8::Intercepted property_getter_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Value>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which,
    v8::Local<v8::Name> js_name = v8::Local<v8::Name>(),
    bool report_declined = true);

v8::Intercepted property_setter_handler(
    PyObject* key,
    v8::Local<v8::Value> js_value,
    const v8::PropertyCallbackInfo<void>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which,
    bool report_declined = true);

v8::Intercepted property_deleter_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Boolean>& info,
    PyObject* self,
    HandlerId which);

v8::Intercepted property_query_handler(
    PyObject* key,
    const v8::PropertyCallbackInfo<v8::Integer>& info,
    PyObject* self,
    HandlerId which);

void named_enumerator_handler(
    const v8::PropertyCallbackInfo<v8::Array>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which);

void indexed_enumerator_handler(
    const v8::PropertyCallbackInfo<v8::Array>& info,
    v8::Local<v8::Context> context,
    PyObject* self,
    HandlerId which);

#endif
