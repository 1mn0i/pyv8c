#include <Python.h>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "pyclass_cross_origin_handlers.h"
#include "pyclass_handlers_internal.h"

#define SETUP                                                         \
    Isolate* isolate = info.GetIsolate();                             \
    HandleScope hs(isolate);                                          \
    Local<Context> context = isolate->GetCurrentContext();            \
    Context::Scope cs(context);

#define Info(T) const PropertyCallbackInfo<T>& info

// Cross-origin callbacks are kept separate from ordinary named/indexed
// handlers because they use the accessing context and a different self lookup.
Intercepted cross_origin_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_getter_handler(
        py_from_js(js_name, context), info, context, self,
        "__v8_cross_origin_name_get__");
}

Intercepted cross_origin_named_setter(
    Local<Name> js_name,
    Local<Value> js_value,
    Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_setter_handler(
        py_from_js(js_name, context), js_value, info, context, self,
        "__v8_cross_origin_name_set__");
}

Intercepted cross_origin_named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(
        py_from_js(js_name, context), info, self,
        "__v8_cross_origin_name_del__");
}

Intercepted cross_origin_named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(
        py_from_js(js_name, context), info, self,
        "__v8_cross_origin_name_query__");
}

void cross_origin_named_enumerator(Info(Array)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    named_enumerator_handler(
        info, context, self, "__v8_cross_origin_name_enum__");
}

Intercepted cross_origin_indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_getter_handler(
        PyLong_FromSize_t(index), info, context, self,
        "__v8_cross_origin_index_get__");
}

Intercepted cross_origin_indexed_setter(
    uint32_t index,
    Local<Value> js_value,
    Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_setter_handler(
        PyLong_FromSize_t(index), js_value, info, context, self,
        "__v8_cross_origin_index_set__");
}

Intercepted cross_origin_indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(
        PyLong_FromSize_t(index), info, self,
        "__v8_cross_origin_index_del__");
}

Intercepted cross_origin_indexed_query(uint32_t index, Info(Integer)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(
        PyLong_FromSize_t(index), info, self,
        "__v8_cross_origin_index_query__");
}

void cross_origin_indexed_enumerator(Info(Array)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);
    indexed_enumerator_handler(
        info, context, self, "__v8_cross_origin_index_enum__");
}

bool cross_origin_access_check(
    v8::Local<v8::Context> accessing_context,
    v8::Local<v8::Object> accessed_object,
    v8::Local<v8::Value> unused_data) {
    context_c* accessing_ctx_c = (context_c*)v8_compat::external_value(
        accessing_context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
    PyObject* accessor = accessing_ctx_c->py_global;

    Local<Context> accessed_context;
    if (!accessed_object->GetCreationContext().ToLocal(&accessed_context))
        return false;

    context_c* interviewee_ctx_c = (context_c*)v8_compat::external_value(
        accessed_context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
    PyObject* interviewee = interviewee_ctx_c->py_global;
    if (PyObject_HasAttrString(interviewee, "__v8_cross_origin_check__")) {
        Py_INCREF(accessor);
        PyObject* value = PyObject_CallMethod(
            interviewee, "__v8_cross_origin_check__", "O", accessor);
        if (value == Py_False)
            return false;
        Py_XDECREF(value);
    }
    return true;
}
