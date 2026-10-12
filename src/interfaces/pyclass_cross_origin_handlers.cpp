#include <Python.h>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "pyclass_cross_origin_handlers.h"
#include "pyclass_handlers.h"

using namespace v8;

// Cross-origin callbacks are kept separate from ordinary named/indexed
// handlers because they use the accessing context and a different self lookup.
Intercepted cross_origin_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_getter_handler(
        py_from_js(js_name, context), info, context, self,
        HandlerId::CrossOriginNameGet);
}

Intercepted cross_origin_named_setter(
    Local<Name> js_name,
    Local<Value> js_value,
    Info(void)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_setter_handler(
        py_from_js(js_name, context), js_value, info, context, self,
        HandlerId::CrossOriginNameSet);
}

Intercepted cross_origin_named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(
        py_from_js(js_name, context), info, self,
        HandlerId::CrossOriginNameDel);
}

Intercepted cross_origin_named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(
        py_from_js(js_name, context), info, self,
        HandlerId::CrossOriginNameQuery);
}

void cross_origin_named_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;

    PyObject* self = get_self_cross_origin(info);
    named_enumerator_handler(
        info, context, self, HandlerId::CrossOriginNameEnum);
}

Intercepted cross_origin_indexed_getter(uint32_t index, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_getter_handler(
        PyLong_FromSize_t(index), info, context, self,
        HandlerId::CrossOriginIndexGet);
}

Intercepted cross_origin_indexed_setter(
    uint32_t index,
    Local<Value> js_value,
    Info(void)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_setter_handler(
        PyLong_FromSize_t(index), js_value, info, context, self,
        HandlerId::CrossOriginIndexSet);
}

Intercepted cross_origin_indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(
        PyLong_FromSize_t(index), info, self,
        HandlerId::CrossOriginIndexDel);
}

Intercepted cross_origin_indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(
        PyLong_FromSize_t(index), info, self,
        HandlerId::CrossOriginIndexQuery);
}

void cross_origin_indexed_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;

    PyObject* self = get_self_cross_origin(info);
    indexed_enumerator_handler(
        info, context, self, HandlerId::CrossOriginIndexEnum);
}

// V8 refuses some accesses itself and reports them through the isolate, never through
// the handlers; the held realm is asked here for the error, raised in the realm that
// asked.  A realm stating no such hook keeps V8's own TypeError.
void cross_origin_failed_access(
    Local<Object> holder,
    AccessType type,
    Local<Value> unused_data) {
    (void)type;
    (void)unused_data;

    Local<Context> accessed_context;
    if (!holder->GetCreationContext().ToLocal(&accessed_context))
        return;
    context_c* accessed_ctx_c = context_from_v8(accessed_context);
    if (accessed_ctx_c == nullptr || accessed_ctx_c->py_global == nullptr)
        return;

    Isolate* isolate = v8_compat::context_isolate(accessed_context);
    if (isolate == nullptr)
        return;
    Local<Context> accessing_context = isolate->GetCurrentContext();
    if (accessing_context.IsEmpty())
        return;
    context_c* accessing_ctx_c = context_from_v8(accessing_context);

    PyObject* interviewee = accessed_ctx_c->py_global;
    PyObject* denied = PyObject_GetAttrString(interviewee, "__v8_cross_origin_denied__");
    if (denied == nullptr) {
        PyErr_Clear();
        return;
    }
    PyObject* argv[] = {
        interviewee,
        accessing_ctx_c != nullptr ? accessing_ctx_c->py_global : Py_None};
    PyObject* value = call_handler(denied, argv, 2);
    Py_DECREF((PyObject*)denied);
    if (value == nullptr) {
        // Keep the realm's own exception pending in the accessing JS realm, where the
        // refused operation ran: it is that realm's DOMException, not V8's TypeError.
        Context::Scope scope(accessing_context);
        js_throw_py();
        return;
    }
    Py_DECREF((PyObject*)value);
}

bool cross_origin_access_check(
    v8::Local<v8::Context> accessing_context,
    v8::Local<v8::Object> accessed_object,
    v8::Local<v8::Value> unused_data) {
    context_c* accessing_ctx_c = context_from_v8(accessing_context);
    if (accessing_ctx_c == nullptr || accessing_ctx_c->py_global == nullptr)
        return false;
    PyObject* accessor = accessing_ctx_c->py_global;

    Local<Context> accessed_context;
    if (!accessed_object->GetCreationContext().ToLocal(&accessed_context))
        return false;

    context_c* interviewee_ctx_c = context_from_v8(accessed_context);
    if (interviewee_ctx_c == nullptr || interviewee_ctx_c->py_global == nullptr)
        return false;
    PyObject* interviewee = interviewee_ctx_c->py_global;
    PyObject* checker = py_class_handler(
        interviewee, HandlerId::CrossOriginCheck, interviewee_ctx_c);
    if (checker == nullptr) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            return true;
        }
        PyErr_Clear();
        return false;
    }
    PyObject* argv[] = {interviewee, accessor};
    PyObject* value = call_handler(checker, argv, 2);
    Py_DECREF((PyObject*)checker);
    if (value == nullptr) {
        // Keep the Python exception pending in the accessing JS realm.  In
        // particular, browser embedders must be able to report SecurityError
        // as a DOMException rather than V8's generic "no access" TypeError.
        Context::Scope scope(accessing_context);
        js_throw_py();
        return false;
    }
    bool allowed = value != Py_False;
    Py_DECREF((PyObject*)value);
    if (!allowed)
        return false;
    return true;
}
