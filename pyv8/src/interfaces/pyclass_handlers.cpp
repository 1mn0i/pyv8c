#include "bridge_base.h"
#include <v8.h>
#include <Python.h>
#include <climits>
#include <sstream>

#include "crossing.h"
#include "convert.h"
#include "error_family.h"
#include "pyclass.h"
#include "idl_spec.h"
#include "pyclass_handlers.h"
#include "trace.h"

using namespace v8;

enum FlagConstructor {
    kNew = 1 << 0,
    kCall = 1 << 1,
};



// The handler an interface declares for *which*, or null when it declares none.
// Both switches below are generated from handler_table.def: the ids are in the
// order of the fields they name, so each stays a jump table.
static PyObject* declared_handler(const IdlSpec& spec, HandlerId which)
{
    switch (which)
    {
#define PYV8_HANDLER(id, declaration, field) case HandlerId::id: return spec.field;
#define PYV8_PROPERTY_NAME_HANDLER(id, declaration) case HandlerId::id:
#include "handler_table.def"
#undef PYV8_HANDLER
#undef PYV8_PROPERTY_NAME_HANDLER
    case HandlerId::Count:
        break;
    }
    return nullptr;
}

// The name one of those handlers is declared under, for the single lookup that
// has to find it as a Python attribute of the receiver: a window keeps its own
// name table, and a receiver whose class has no template here can still declare
// the handler on itself.
static const char* handler_declaration_name(HandlerId which)
{
    switch (which)
    {
#define PYV8_HANDLER(id, declaration, field) case HandlerId::id: return declaration;
#define PYV8_PROPERTY_NAME_HANDLER(id, declaration) case HandlerId::id: return declaration;
#include "handler_table.def"
#undef PYV8_HANDLER
#undef PYV8_PROPERTY_NAME_HANDLER
    case HandlerId::Count:
        break;
    }
    return nullptr;
}

//: The template of the interface a receiver was made from, or null when it has none.
static PyObject* template_of(PyObject* receiver, context_c* context)
{
    if (receiver == nullptr || receiver == Py_None || context == nullptr)
        return nullptr;
    PyObject* name = PyObject_GetAttrString((PyObject*)Py_TYPE((PyObject*)receiver), "__name__");
    if (name == nullptr) { PyErr_Clear(); return nullptr; }
    PyObject* cached = context_registry_get(context, name, REGISTRY_TEMPLATE);
    Py_DECREF(name);
    return cached;
}

PyObject* py_class_handler(PyObject* receiver, HandlerId which, context_c* context)
{
    if (receiver == nullptr) { PyErr_SetString(PyExc_RuntimeError, "callback receiver is unavailable"); return nullptr; }

    const bool named = which == HandlerId::PropertyNameGet || which == HandlerId::PropertyNameQuery;
    if (!named) {
        PyObject* cached = template_of(receiver, context);
        if (cached != nullptr && ((py_class*)cached)->idl_spec != nullptr) {
            // The interface is the authority: a handler it does not declare is a
            // decline, and nothing is looked up on the receiver.
            PyObject* callback = declared_handler(*(IdlSpec*)((py_class*)cached)->idl_spec, which);
            if (callback == nullptr)
                return nullptr;
            // The declaration's reference is the interface's; the caller is
            // handed one of its own, which is what every path here promises.
            Py_INCREF(callback);
            return callback;
        }
    }
    const char* name = handler_declaration_name(which);
    if (name == nullptr)
        return nullptr;
    PyObject* method = PyObject_GetAttrString(receiver, name);
    if (method == nullptr && PyErr_ExceptionMatches(PyExc_AttributeError)) PyErr_Clear();
    return method;
}

// argv[0] holds the receiver.  The declarations this binding installs are plain
// functions taking it, while a bound method already carries one.
PyObject* call_handler(PyObject* handler, PyObject* argv[], size_t count)
{
    if (PyMethod_Check_abi3(handler))
        return PyObject_Vectorcall(handler, argv + 1, count - 1, nullptr);
    return PyObject_Vectorcall(handler, argv, count, nullptr);
}

static PyObject* get_handler_method(PyObject* self, HandlerId which)
{
    auto* isolate = v8::Isolate::GetCurrent();
    auto current = isolate->GetCurrentContext();
    auto* context = context_from_v8(current);
    return py_class_handler(self, which, context);
}

// Legacy platform objects report failed writes/deletes through the V8
// callback result.  Python callbacks cannot see ShouldThrowOnError(), so a
// declared exception is suppressed in sloppy mode and propagated in strict
// mode by the native bridge.
static bool
consume_legacy_property_error(bool should_throw)
{
    if (!PyErr_Occurred())
        return false;
    PyObject *exc_type = NULL, *exc_value = NULL, *exc_traceback = NULL;
    PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);
    PyErr_NormalizeException(&exc_type, &exc_value, &exc_traceback);
    int marked = error_family::is_interceptor_failure(exc_value);
    // fprintf(stderr, "legacy marked=%d strict=%d\\n", marked, should_throw);
    if (marked && !should_throw) {
        Py_XDECREF((PyObject*)exc_type);
        Py_XDECREF((PyObject*)exc_value);
        Py_XDECREF((PyObject*)exc_traceback);
        return true;
    }
    PyErr_Restore(exc_type, exc_value, exc_traceback);
    return false;
}

bool
property_query_value(PyObject* key, PyObject* self, HandlerId which,
                     long* attributes)
{
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        if (!PyErr_Occurred())
            return false;
        return false;
    }
    PyObject* argv[] = {self, key};
    PyObject* result = call_handler(handler, argv, 2);
    Py_DECREF((PyObject*)handler);
    if (result == NULL)
        return false;
    if (result == Py_None) {
        Py_DECREF((PyObject*)result);
        return false;
    }
    if (!PyLong_Check(result)) {
        Py_DECREF((PyObject*)result);
        return false;
    }
    *attributes = PyLong_AsLong(result);
    Py_DECREF((PyObject*)result);
    return !PyErr_Occurred();
}

Intercepted
property_descriptor(PyObject* key, const PropertyCallbackInfo<Value>& info,
                    Local<Context> context, PyObject* self,
                    HandlerId which, bool enumerable)
{
    // A descriptor is not a property read: resolving it through the traced
    // getter reports an extra event for a single read, because the engine asks
    // for the descriptor *and* performs the read (``typeof``, ``in`` and
    // ``getOwnPropertyDescriptor`` all do).
    Intercepted intercepted = property_getter_handler_impl(
        key, info, context, self, which);
    if (intercepted == Intercepted::kNo)
        return intercepted;
    Local<Value> value = info.GetReturnValue().Get();
    Local<Object> descriptor = Object::New(info.GetIsolate());
    bool worked;
    if (!descriptor->Set(context, V8String(info.GetIsolate(), "value"), value).To(&worked) ||
        !descriptor->Set(context, V8String(info.GetIsolate(), "writable"),
                          Boolean::New(info.GetIsolate(), false)).To(&worked) ||
        !descriptor->Set(context, V8String(info.GetIsolate(), "enumerable"),
                          Boolean::New(info.GetIsolate(), enumerable)).To(&worked) ||
        !descriptor->Set(context, V8String(info.GetIsolate(), "configurable"),
                          Boolean::New(info.GetIsolate(), true)).To(&worked)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to create property descriptor");
        js_throw_py();
        return Intercepted::kYes;
    }
    info.GetReturnValue().Set(descriptor);
    return Intercepted::kYes;
}

Intercepted
property_getter_handler_impl(PyObject* key,
                        const PropertyCallbackInfo<Value>& info,
                        Local<Context> context,
                        PyObject* self,
                        HandlerId which)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        Py_DECREF((PyObject*)key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* argv[] = {self, key};
    PyObject* result = call_handler(handler, argv, 2);
    Py_DECREF((PyObject*)handler);
    Py_DECREF((PyObject*)key);
    if (result == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (result == Py_None) {
        Py_DECREF((PyObject*)result);
        return Intercepted::kNo;
    }
    Local<Value> js_result = js_from_py(result, context);
    Py_DECREF((PyObject*)result);
    if (js_result.IsEmpty() || PyErr_Occurred()) {
        js_throw_py();
        return Intercepted::kNo;
    }
    info.GetReturnValue().Set(js_result);
    return Intercepted::kYes;
}

Intercepted
property_setter_handler_impl(PyObject* key,
                        Local<Value> js_value,
                        const PropertyCallbackInfo<v8_compat::PropertySetterResult>& info,
                        Local<Context> context,
                        PyObject* self,
                        HandlerId which)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* value = py_from_js(js_value, context);
    if (value == NULL) {
        Py_DECREF((PyObject*)key);
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        Py_DECREF((PyObject*)key);
        Py_DECREF((PyObject*)value);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* argv[] = {self, key, value};
    PyObject* result = call_handler(handler, argv, 3);
    Py_DECREF((PyObject*)handler);
    Py_DECREF((PyObject*)key);
    Py_DECREF((PyObject*)value);
    if (result == NULL) {
        if (consume_legacy_property_error(info.ShouldThrowOnError())) {
            v8_compat::setter_rejected(info);
            return Intercepted::kYes;
        }
        js_throw_py();
        return Intercepted::kNo;
    }
    if (result == Py_None) {
        Py_DECREF((PyObject*)result);
        return Intercepted::kNo;
    }
    Py_DECREF((PyObject*)result);
    return Intercepted::kYes;
}

static bool declares_attribute(PyObject* attributes, PyObject* name, bool setter) {
    if (attributes == nullptr) return false;
    Py_ssize_t size = PySequence_Size(attributes);
    for (Py_ssize_t index = 0; index < size; ++index) {
        PyObject* item = PySequence_GetItem(attributes, index);
        if (item == nullptr) {
            PyErr_Clear();
            continue;
        }
        auto* attribute = (AttributeSpec*)item;
        const bool matches = attribute->name != nullptr &&
            (setter ? attribute->setter != nullptr : attribute->getter != nullptr) &&
            PyObject_RichCompareBool(attribute->name, name, Py_EQ) == 1;
        Py_DECREF((PyObject*)item);
        if (matches) return true;
    }
    return false;
}

static bool declares_operation(PyObject* methods, PyObject* name) {
    if (methods == nullptr) return false;
    Py_ssize_t size = PySequence_Size(methods);
    for (Py_ssize_t index = 0; index < size; ++index) {
        PyObject* item = PySequence_GetItem(methods, index);
        if (item == nullptr) {
            PyErr_Clear();
            continue;
        }
        auto* method = (MethodSpec*)item;
        const bool matches = method->name != nullptr &&
            PyObject_RichCompareBool(method->name, name, Py_EQ) == 1;
        Py_DECREF((PyObject*)item);
        if (matches) return true;
    }
    return false;
}

// Does the receiver's interface declare this name?  Declared members observe
// themselves, so reporting them here too would log every access twice.
static bool declares_member(PyObject* receiver, context_c* context, PyObject* name, bool setter) {
    if (receiver == nullptr || receiver == Py_None || context == nullptr || name == nullptr) return false;
    if (!PyUnicode_Check(name)) return false;
    PyObject* cached = template_of(receiver, context);
    if (cached == nullptr || ((py_class*)cached)->idl_spec == nullptr) {
        PyErr_Clear();
        return false;
    }
    const auto& spec = *(IdlSpec*)((py_class*)cached)->idl_spec;
    if (declares_attribute(spec.attributes, name, setter)) return true;
    return !setter && declares_operation(spec.methods, name);
}

// What the engine produced for a declined name, or an empty handle when getting
// it would run JavaScript.  Only real data properties are read: resolving an
// accessor would run the getter the engine is about to run.
static Local<Value> declined_property_value(const PropertyCallbackInfo<Value>& info,
                                            Local<Name> js_name, Local<Context> context) {
    if (js_name.IsEmpty()) return Local<Value>();
    Local<Object> object = v8_compat::callback_holder(info);
    while (!object.IsEmpty()) {
        if (object->HasRealNamedProperty(context, js_name).FromMaybe(false)) {
            if (object->HasRealNamedCallbackProperty(context, js_name).FromMaybe(true)) return Local<Value>();
            Local<Value> value;
            if (!object->GetRealNamedProperty(context, js_name).ToLocal(&value)) return Local<Value>();
            return value;
        }
        Local<Value> prototype = object->GetPrototype();
        object = prototype->IsObject() ? prototype.As<Object>() : Local<Object>();
    }
    return Local<Value>();
}

Intercepted property_getter_handler(PyObject* key, const PropertyCallbackInfo<Value>& info,
                                    Local<Context> context, PyObject* self, HandlerId which,
                                    Local<Name> js_name, bool report_declined) {
    auto* owner = context_from_v8(context);
    if (!context_trace_enabled(owner)) return property_getter_handler_impl(key, info, context, self, which);
    Py_XINCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_getter_handler_impl(key, info, context, self, which);
    if (result == Intercepted::kYes || observation.failed()) {
        observation.emit(0, key, info.GetReturnValue().Get());
    } else if (report_declined && self != Py_None && !declares_member(self, owner, key, false)) {
        // Nothing was intercepted, so the engine resolves the name outside this
        // binding: a native global such as ``eval``, a JavaScript function or an
        // expando.  Report the value that resolution produces, without asking
        // JavaScript for it a second time.
        Local<Value> resolved = declined_property_value(info, js_name, context);
        if (!resolved.IsEmpty()) observation.emit(0, key, resolved);
    }
    Py_XDECREF((PyObject*)key);
    return result;
}

Intercepted property_setter_handler(PyObject* key, Local<Value> value, const PropertyCallbackInfo<v8_compat::PropertySetterResult>& info,
                                    Local<Context> context, PyObject* self, HandlerId which,
                                    bool report_declined) {
    auto* owner = context_from_v8(context);
    if (!context_trace_enabled(owner)) return property_setter_handler_impl(key, value, info, context, self, which);
    Py_XINCREF((PyObject*)key);
    trace::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_setter_handler_impl(key, value, info, context, self, which);
    if (result == Intercepted::kYes || observation.failed()) {
        observation.emit(1, key, value);
    } else if (report_declined && self != Py_None && !declares_member(self, owner, key, true)) {
        // A declined write lands on a JavaScript property (an expando or a
        // JavaScript accessor) or is dropped by the engine; the assigned value is
        // already at hand, so it is reported as is.
        observation.emit(1, key, value);
    }
    Py_XDECREF((PyObject*)key);
    return result;
}

Intercepted
property_deleter_handler(PyObject* key,
                         const PropertyCallbackInfo<Boolean>& info,
                         PyObject* self,
                         HandlerId which)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        Py_DECREF((PyObject*)key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* argv[] = {self, key};
    PyObject* result = call_handler(handler, argv, 2);
    Py_DECREF((PyObject*)handler);
    Py_DECREF((PyObject*)key);
    if (result == NULL) {
        if (consume_legacy_property_error(info.ShouldThrowOnError())) {
            info.GetReturnValue().Set(false);
            return Intercepted::kYes;
        }
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!PyBool_Check(result)) {
        Py_DECREF((PyObject*)result);
        return Intercepted::kNo;
    }
    info.GetReturnValue().Set(Boolean::New(info.GetIsolate(), result == Py_True));
    Py_DECREF((PyObject*)result);
    return Intercepted::kYes;
}

Intercepted
property_query_handler(PyObject* key,
                       const PropertyCallbackInfo<Integer>& info,
                       PyObject* self,
                       HandlerId which)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        Py_DECREF((PyObject*)key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* argv[] = {self, key};
    PyObject* result = call_handler(handler, argv, 2);
    Py_DECREF((PyObject*)handler);
    Py_DECREF((PyObject*)key);
    if (result == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!PyLong_Check(result)) {
        Py_DECREF((PyObject*)result);
        return Intercepted::kNo;
    }
    long attributes = PyLong_AsLong(result);
    Py_DECREF((PyObject*)result);
    if (PyErr_Occurred()) {
        js_throw_py();
        return Intercepted::kNo;
    }
    info.GetReturnValue().Set(Integer::New(info.GetIsolate(), attributes));
    return Intercepted::kYes;
}

void
named_enumerator_handler(const PropertyCallbackInfo<Array>& info,
                         Local<Context> context,
                         PyObject* self,
                         HandlerId which)
{
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        if (PyErr_Occurred())
            js_throw_py();
        return;
    }
    PyObject* argv[] = {self};
    PyObject* keys = call_handler(handler, argv, 1);
    Py_DECREF((PyObject*)handler);
    if (keys == NULL) {
        js_throw_py();
        return;
    }
    if (keys == Py_None || !PySequence_Check(keys)) {
        Py_DECREF((PyObject*)keys);
        return;
    }
    Py_ssize_t length = PySequence_Length(keys);
    if (length < 0 || length > INT_MAX) {
        Py_DECREF((PyObject*)keys);
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_OverflowError, "handler key sequence is too large");
        js_throw_py();
        return;
    }
    Local<Array> js_keys = Array::New(info.GetIsolate(), (int)length);
    for (Py_ssize_t i = 0; i < length; ++i) {
        PyObject* item = PySequence_GetItem(keys, i);
        if (item == NULL) {
            Py_DECREF((PyObject*)keys);
            js_throw_py();
            return;
        }
        Local<Value> js_item = js_from_py(item, context);
        Py_DECREF((PyObject*)item);
        if (js_item.IsEmpty() || PyErr_Occurred()) {
            Py_DECREF((PyObject*)keys);
            js_throw_py();
            return;
        }
        bool set_worked;
        if (!js_keys->Set(context, (uint32_t)i, js_item).To(&set_worked)) {
            Py_DECREF((PyObject*)keys);
            PyErr_SetString(PyExc_RuntimeError, "failed to set handler enumeration key");
            js_throw_py();
            return;
        }
    }
    Py_DECREF((PyObject*)keys);
    info.GetReturnValue().Set(js_keys);
}

void
indexed_enumerator_handler(const PropertyCallbackInfo<Array>& info,
                           Local<Context> context,
                           PyObject* self,
                           HandlerId which)
{
    PyObject* handler = get_handler_method(self, which);
    if (handler == NULL) {
        if (PyErr_Occurred())
            js_throw_py();
        return;
    }
    PyObject* argv[] = {self};
    PyObject* py_length = call_handler(handler, argv, 1);
    Py_DECREF((PyObject*)handler);
    if (py_length == NULL) {
        js_throw_py();
        return;
    }
    if (py_length == Py_None || !PyLong_Check(py_length)) {
        Py_DECREF((PyObject*)py_length);
        return;
    }
    long length = PyLong_AsLong(py_length);
    Py_DECREF((PyObject*)py_length);
    if (length < 0 || length > INT_MAX || PyErr_Occurred()) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_OverflowError, "handler index length is out of range");
        js_throw_py();
        return;
    }
    Local<Array> keys = Array::New(info.GetIsolate(), (int)length);
    for (int i = 0; i < length; ++i) {
        bool set_worked;
        if (!keys->Set(context, i, Integer::New(info.GetIsolate(), i)).To(&set_worked)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to set handler enumeration index");
            js_throw_py();
            return;
        }
    }
    info.GetReturnValue().Set(keys);
}
