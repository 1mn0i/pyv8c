#include "bridge_base.h"
#include <Python.h>
#include "crossing.h"
#include <v8.h>

#include "convert.h"
#include "context.h"
#include "pyclass_handlers.h"
#include "trace.h"

using namespace v8;

Intercepted named_getter(Local<Name> js_name, Info(Value)) {
    SETUP
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = context_from_v8(context);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_getter_handler(py_from_js(js_name, context), info, context, self, HandlerId::NameGet, js_name);
}

// Named-properties objects belong to their creation realm, not whichever
// realm happens to invoke a borrowed prototype. Bypass interceptors when
// checking ordinary properties to avoid recursion through this handler.
template <class T>
static PyObject* properties_owner(const PropertyCallbackInfo<T>& info, Local<Name> name) {
    Local<Context> owner;
    Local<Object> holder = v8_compat::callback_holder(info);
    if (!holder->GetCreationContext().ToLocal(&owner)) return Py_None;
    auto* ctx = context_from_v8(owner);
    if (ctx == nullptr || ctx->py_global == nullptr) return Py_None;
    Local<Object> global = owner->Global();
    if (global->HasRealNamedProperty(owner, name).FromMaybe(false)) return Py_None;
    Local<Value> proto = global->GetPrototype();
    while (proto->IsObject()) {
        auto object = proto.As<Object>();
        if (object != holder && object->HasRealNamedProperty(owner, name).FromMaybe(false))
            return Py_None;
        proto = object->GetPrototype();
    }
    return ctx->py_global;
}

Intercepted properties_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    PyObject* self = properties_owner(info, js_name);
    // This interceptor is the fallback layer behind the per-class one, so a name
    // it declined is reported there already; reporting it here too would log
    // every unknown global read twice.
    return property_getter_handler(py_from_js(js_name, context), info, context, self,
        HandlerId::PropertyNameGet, js_name, false);
}

Intercepted properties_named_query(Local<Name> name, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    return property_query_handler(py_from_js(name, context), info,
        properties_owner(info, name), HandlerId::PropertyNameQuery);
}

Intercepted properties_named_descriptor(Local<Name> name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto result = property_getter_handler_impl(py_from_js(name, context), info, context,
        properties_owner(info, name), HandlerId::PropertyNameGet);
    if (result == Intercepted::kNo) return result;
    Local<Value> value = info.GetReturnValue().Get();
    Local<Object> descriptor = Object::New(isolate);
    if (!descriptor->CreateDataProperty(context, JSTR("value"), value).FromMaybe(false) ||
        !descriptor->CreateDataProperty(context, JSTR("writable"), True(isolate)).FromMaybe(false) ||
        !descriptor->CreateDataProperty(context, JSTR("enumerable"), True(isolate)).FromMaybe(false) ||
        !descriptor->CreateDataProperty(context, JSTR("configurable"), True(isolate)).FromMaybe(false))
        return Intercepted::kYes;
    info.GetReturnValue().Set(descriptor);
    return Intercepted::kYes;
}

static thread_local bool deleting_synthetic_constructor = false;

void properties_delete_constructor(Local<Object> prototype, Local<Context> context) {
    const bool previous = deleting_synthetic_constructor;
    deleting_synthetic_constructor = true;
    bool worked;
    prototype->Delete(context, V8String(v8_compat::context_isolate(context), "constructor")).To(&worked);
    deleting_synthetic_constructor = previous;
}

Intercepted properties_named_deleter(Local<Name>, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    if (deleting_synthetic_constructor) return Intercepted::kNo;
    info.GetReturnValue().Set(false);
    if (info.ShouldThrowOnError()) info.GetIsolate()->ThrowException(
        Exception::TypeError(JSTR("Cannot delete properties on WindowProperties")));
    return Intercepted::kYes;
}

Intercepted properties_named_definer(Local<Name>, const PropertyDescriptor&, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    info.GetReturnValue().Set(false);
    if (info.ShouldThrowOnError()) info.GetIsolate()->ThrowException(
        Exception::TypeError(JSTR("Cannot define properties on WindowProperties")));
    return Intercepted::kYes;
}

void properties_named_enumerator(Info(Array)) {
    info.GetReturnValue().Set(Array::New(info.GetIsolate(), 0));
}

//: The name an indexed property answers to.  The digits are built on the stack: the
//: ``std::to_string`` this replaced allocated a string for every interception.
static Local<Name> indexed_name(Isolate* isolate, uint32_t index) {
    char digits[11];
    unsigned length = 0;
    if (index == 0) {
        digits[length++] = '0';
    }
    else {
        char reversed[10];
        unsigned count = 0;
        for (uint32_t rest = index; rest != 0; rest /= 10)
            reversed[count++] = static_cast<char>('0' + rest % 10);
        while (count != 0)
            digits[length++] = reversed[--count];
    }
    digits[length] = '\0';
    return V8String(isolate, digits);
}

Intercepted properties_indexed_getter(uint32_t index, Info(Value)) {
    return properties_named_getter(indexed_name(info.GetIsolate(), index), info);
}
Intercepted properties_indexed_query(uint32_t index, Info(Integer)) {
    return properties_named_query(indexed_name(info.GetIsolate(), index), info);
}
Intercepted properties_indexed_descriptor(uint32_t index, Info(Value)) {
    return properties_named_descriptor(indexed_name(info.GetIsolate(), index), info);
}
Intercepted properties_indexed_deleter(uint32_t index, Info(Boolean)) {
    return properties_named_deleter(indexed_name(info.GetIsolate(), index), info);
}
Intercepted properties_indexed_definer(uint32_t index, const PropertyDescriptor& desc, Info(Boolean)) {
    return properties_named_definer(indexed_name(info.GetIsolate(), index), desc, info);
}


Intercepted named_setter(Local<Name> js_name, Local<Value> js_value, Info(v8_compat::PropertySetterResult)) {
    SETUP
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = context_from_v8(context);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, HandlerId::NameSet);
}

Intercepted named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    PyObject* self = get_self(info);
    return property_deleter_handler(py_from_js(js_name, context), info, self, HandlerId::NameDel);
}

Intercepted named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = context_from_v8(context);
    PyObject* self = get_self(info);
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_query_handler(py_from_js(js_name, context), info, self, HandlerId::NameQuery);
    PyObject* key = py_from_js(js_name, context);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    Py_INCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_query_handler(key, info, self, HandlerId::NameQuery);
    // ``'name' in target`` is an existence probe, not a read: it is reported as
    // its own event carrying the answer the engine reaches for this name.
    observation.emit(4, key, Boolean::New(isolate, result == Intercepted::kYes ||
                                                    real_property_exists(info, js_name, context)));
    Py_DECREF((PyObject*)key);
    return result;
}

void named_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;
    PyObject* self = get_self(info);
    named_enumerator_handler(info, context, self, HandlerId::NameEnum);
}

Intercepted indexed_getter(uint32_t index, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = context_from_v8(context);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_getter_handler(PyLong_FromSize_t(index), info, context, self, HandlerId::IndexGet);
}

Intercepted indexed_setter(uint32_t index, Local<Value> js_value, Info(v8_compat::PropertySetterResult)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = context_from_v8(context);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, HandlerId::IndexSet);
}

Intercepted indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    PyObject* self = get_self(info);
    return property_deleter_handler(PyLong_FromSize_t(index), info, self, HandlerId::IndexDel);
}

Intercepted indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = context_from_v8(context);
    PyObject* self = get_self(info);
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_query_handler(PyLong_FromSize_t(index), info, self, HandlerId::IndexQuery);
    PyObject* key = PyLong_FromSize_t(index);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    Py_INCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_query_handler(key, info, self, HandlerId::IndexQuery);
    const bool exists = v8_compat::callback_holder(info)
        ->HasRealIndexedProperty(context, index).FromMaybe(false);
    observation.emit(4, key, Boolean::New(isolate, result == Intercepted::kYes || exists));
    Py_DECREF((PyObject*)key);
    return result;
}

void indexed_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;
    PyObject* self = get_self(info);
    indexed_enumerator_handler(info, context, self, HandlerId::IndexEnum);
}

Intercepted named_descriptor(Local<Name> js_name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = context_from_v8(context);
    Local<Object> receiver = info.Holder();
    PyObject* self = get_self(info);
    const bool report = context_trace_enabled(owner) && self != Py_None;
    if (receiver->GetRealNamedPropertyAttributesInPrototypeChain(context, js_name).IsJust()) {
        // A real property on the prototype chain answers this probe; the engine
        // resolves it, and the probe itself is what gets reported here.
        if (report) {
            PyObject* key = py_from_js(js_name, context);
            if (key == NULL) {
                js_throw_py();
                return Intercepted::kNo;
            }
            trace::TraceEvent observation(info.GetIsolate(), owner, self);
            observation.emit(5, key, Boolean::New(isolate, own_real_property_exists(info, js_name, context)));
            Py_DECREF((PyObject*)key);
        }
        return Intercepted::kNo;
    }
    PyObject* key = py_from_js(js_name, context);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!report) return property_descriptor(key, info, context, self, HandlerId::NameGet, false);
    Py_INCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_descriptor(key, info, context, self, HandlerId::NameGet, false);
    // A descriptor probe asks what the own property is, not what it holds, so it
    // is reported as its own event and never as a getter event.
    observation.emit(5, key, Boolean::New(isolate, result == Intercepted::kYes ||
                                                    own_real_property_exists(info, js_name, context)));
    Py_DECREF((PyObject*)key);
    return result;
}

Intercepted indexed_descriptor(uint32_t index, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = context_from_v8(context);
    PyObject* self = get_self(info);
    PyObject* key = PyLong_FromSize_t(index);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_descriptor(key, info, context, self, HandlerId::IndexGet, true);
    Py_INCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_descriptor(key, info, context, self, HandlerId::IndexGet, true);
    const bool exists = v8_compat::callback_holder(info)
        ->HasRealIndexedProperty(context, index).FromMaybe(false);
    observation.emit(5, key, Boolean::New(isolate, result == Intercepted::kYes || exists));
    Py_DECREF((PyObject*)key);
    return result;
}

static Intercepted
property_definer(PyObject* key, const PropertyDescriptor& desc,
                 const PropertyCallbackInfo<Boolean>& info,
                 PyObject* self, HandlerId query_method,
                 const char* error_message)
{
    long attributes = 0;
    bool exists = property_query_value(key, self, query_method, &attributes);
    if (PyErr_Occurred()) {
        js_throw_py();
        return Intercepted::kYes;
    }
    if (!exists)
        return Intercepted::kNo;
    info.GetReturnValue().Set(false);
    if (info.ShouldThrowOnError()) {
        Isolate* isolate = info.GetIsolate();
        info.GetIsolate()->ThrowException(
            Exception::TypeError(JSTR(error_message)));
    }
    return Intercepted::kYes;
}

Intercepted named_definer(Local<Name> js_name, const PropertyDescriptor& desc,
                          const PropertyCallbackInfo<Boolean>& info) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self(info);
    PyObject* key = py_from_js(js_name, context);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kYes;
    }
    Intercepted result = property_definer(
        key, desc, info, self, HandlerId::NameQuery,
        "Named property setter is not supported.");
    Py_DECREF((PyObject*)key);
    return result;
}

Intercepted indexed_definer(uint32_t index, const PropertyDescriptor& desc,
                            const PropertyCallbackInfo<Boolean>& info) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    PyObject* self = get_self(info);
    PyObject* key = PyLong_FromSize_t(index);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kYes;
    }
    Intercepted result = property_definer(
        key, desc, info, self, HandlerId::IndexQuery,
        "Indexed property setter is not supported.");
    Py_DECREF((PyObject*)key);
    return result;
}
