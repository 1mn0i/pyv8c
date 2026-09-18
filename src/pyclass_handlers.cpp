#include <v8.h>
#include <Python.h>
#include <climits>
#include <sstream>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "idl_spec.h"
#include "pyclass_handlers_internal.h"
#include "faker/trace.h"

using namespace v8;

enum FlagConstructor {
    kDontAll = 0,
    kNew = 1 << 0,
    kCall = 1 << 1,
    kAll = kNew | kCall,
};

#define SETUP                                                         \
    Isolate* isolate = info.GetIsolate();                             \
    HandleScope hs(isolate);                                          \
    Local<Context> context = isolate->GetCurrentContext();            \
    Context::Scope cs(context);  



#define Info(T) const PropertyCallbackInfo<T> &info


static void
py_class_construct_impl(const FunctionCallbackInfo<Value>& info,
                        Local<Context> context,
                        py_class* self,
                        context_c* py_context)
{
    Isolate* isolate = info.GetIsolate();
    bool can_new = (self->constructor_behavior & FlagConstructor::kNew) != 0;
    bool can_call = (self->constructor_behavior & FlagConstructor::kCall) != 0;
    if (!can_call && !can_new) {
        info.GetIsolate()->ThrowException(Exception::TypeError(JSTR("Illegal constructor")));
        return;
    }
    if (!can_call && !info.IsConstructCall()) {
        std::ostringstream message;
        message << "Failed to construct '" << PyUnicode_AsUTF8_abi3(self->cls_name)
                << "': Please use the 'new' operator, this DOM object constructor "
                   "cannot be called as a function.";
        info.GetIsolate()->ThrowException(Exception::TypeError(JSTR(message.str().c_str())));
        return;
    }
    if (self->unconstructable) {
        PyObject* message = PyUnicode_FromFormat("%S is not a constructor", self->cls_name);
        if (message == NULL) {
            js_throw_py();
            return;
        }
        Local<Value> js_message = js_from_py(message, context);
        Py_DECREF(message);
        if (js_message.IsEmpty() || PyErr_Occurred()) {
            js_throw_py();
            return;
        }
        info.GetIsolate()->ThrowException(Exception::TypeError(js_message.As<String>()));
        return;
    }

    PyObject* kwargs = PyDict_New();
    if (kwargs == NULL) {
        js_throw_py();
        return;
    }
    if (PyDict_SetItemString(kwargs, "win", py_context->py_global) < 0) {
        Py_DECREF(kwargs);
        js_throw_py();
        return;
    }
    PyObject* args = pys_from_jss(info, context);
    if (args == NULL) {
        Py_DECREF(kwargs);
        js_throw_py();
        return;
    }
    PyObject* new_object = self->constructor_adapter != NULL
        ? PyObject_CallFunctionObjArgs(
            self->constructor_adapter, self->cls, args, kwargs, NULL)
        : PyObject_Call(self->cls, args, kwargs);
    Py_DECREF(args);
    Py_DECREF(kwargs);
    if (new_object == NULL) {
        js_throw_py();
        return;
    }
    if (!py_class_init_js_object(
            v8_compat::callback_holder(info), new_object, context)) {
        Py_DECREF(new_object);
        js_throw_py();
        return;
    }
    Py_DECREF(new_object);
}


void py_class_construct_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;

    py_class *self = (py_class *) IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return;
    if (context_trace_enabled(ctx_c)) {
        {
            faker::TraceScope construct_trace(info, 3, self->cls_name, self->cls, ctx_c);
            py_class_construct_impl(info, context, self, ctx_c);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else {
        py_class_construct_impl(info, context, self, ctx_c);
    }
}

static void py_class_method_impl(const FunctionCallbackInfo<Value> &info, Local<Context> context,
                                 PyObject *receiver, MethodSpec *binding)
{
    PyObject *callable = binding->callback;
    Py_INCREF(callable);
    int offset = binding->pass_receiver && receiver != Py_None ? 1 : 0;
    PyObject *args = PyTuple_New(info.Length() + offset);
    if (args == nullptr)
    {
        Py_DECREF(callable);
        js_throw_py();
        return;
    }
    if (offset)
    {
        Py_INCREF(receiver);
        if (PyTuple_SetItem(args, 0, receiver) < 0)
        {
            Py_DECREF(args);
            Py_DECREF(callable);
            js_throw_py();
            return;
        }
    }
    for (int index = 0; index < info.Length(); ++index)
    {
        PyObject *value = py_argument_from_js(info[index], context);
        if (value == nullptr || PyTuple_SetItem(args, index + offset, value) < 0)
        {
            Py_DECREF(args);
            Py_DECREF(callable);
            js_throw_py();
            return;
        }
    }
    PyObject *result = PyObject_CallObject(callable, args);
    Py_DECREF(args);
    Py_DECREF(callable);
    if (result == nullptr)
    {
        js_throw_py();
        return;
    }
    Local<Value> converted = js_from_py(result, context);
    Py_DECREF(result);
    if (converted.IsEmpty() || PyErr_Occurred())
    {
        js_throw_py();
        return;
    }
    info.GetReturnValue().Set(converted);
}

void py_class_method_callback(const FunctionCallbackInfo<Value> &info)
{
    SETUP;
    if (!context_check_callback(context)) return;
    auto *binding = (MethodSpec *)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject *receiver = binding->pass_receiver ? get_self(info) : Py_None;
    if (receiver == nullptr)
    {
        js_throw_py();
        return;
    }
    context_c *owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (owner == nullptr)
        return;
    if (context_trace_enabled(owner))
    {
        PyObject *trace_receiver = receiver;
        if (trace_receiver == Py_None && PyMethod_Check_abi3(binding->callback))
            trace_receiver = PyMethod_GET_SELF_abi3(binding->callback);
        {
            faker::TraceScope operation_trace(info, 2, binding->name, trace_receiver, owner);
            py_class_method_impl(info, context, receiver, binding);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else
    {
        py_class_method_impl(info, context, receiver, binding);
    }
}
// --- Interceptors ---
void py_class_property_getter(Local<Name> js_name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return;

    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)IsolateState::callback_target(info.Data());
    if (method == nullptr) { js_throw_py(); return; }
    JS_PROPAGATE_PY(method);


    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }

    PyObject* set_method_name = PyObject_GetAttrString(method, "__name__");
    PyObject* replace_from = PyUnicode_FromString("set_");
    PyObject* replace_to = PyUnicode_FromString("get_");
    PyObject* get_method_name = PyUnicode_Replace(set_method_name,  replace_from, replace_to, 1);
    Py_XDECREF(set_method_name);
    Py_XDECREF(replace_from);
    Py_XDECREF(replace_to);

    PyObject* result = PyObject_CallMethodNoArgs_abi3(self, get_method_name);
    Py_XDECREF(get_method_name);
    JS_PROPAGATE_PY(result);
    info.GetReturnValue().Set(js_from_py(result, context));
    Py_XDECREF(result);
}

void py_class_property_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP;
    if (!context_check_callback(context)) return;

    PyObject* self = get_self(info);
    PyObject* method = (PyObject*)IsolateState::callback_target(info.Data());
    if (method == nullptr) { js_throw_py(); return; }
    JS_PROPAGATE_PY(method);


    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }


    PyObject* value = py_from_js(js_value, context);
    JS_PROPAGATE_PY(value);

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    PyObject* result = PyObject_CallMethodOneArg_abi3(self, method_name, value);
    Py_XDECREF(value);
    Py_XDECREF(method_name);
    Py_XDECREF(result);
    JS_PROPAGATE_PY(result);
}

static void attribute_get_impl(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* self = binding->getter_receiver ? get_self(info) : Py_None;
    PyObject* result = self == Py_None ? PyObject_CallNoArgs(binding->getter) :
        PyObject_CallFunctionObjArgs(binding->getter, self, nullptr);
    JS_PROPAGATE_PY(result);
    auto value = js_from_py(result, context);
    Py_DECREF(result);
    if (value.IsEmpty() || PyErr_Occurred()) { js_throw_py(); return; }
    info.GetReturnValue().Set(value);
}

void py_attribute_for_get(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (!context_trace_enabled(owner)) { attribute_get_impl(info); return; }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* receiver = binding->getter_receiver ? get_self(info) : Py_None;
    faker::TraceScope observation(info, 0, binding->name, receiver, owner);
    attribute_get_impl(info);
}

static void attribute_set_impl(const FunctionCallbackInfo<Value>& info, bool instance) {
    SETUP;
    if (!context_check_callback(context)) return;
    if (info.Length() < 1) {
        isolate->ThrowException(v8::Exception::TypeError(V8String(isolate, "1 argument required, but only 0 present")));
        return;
    }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* self = binding->setter_receiver ? get_self(info) : Py_None;
    PyObject* value = py_argument_from_js(info[0], context);
    JS_PROPAGATE_PY(value);
    PyObject* result = self == Py_None ? PyObject_CallFunctionObjArgs(binding->setter, value, nullptr) :
        PyObject_CallFunctionObjArgs(binding->setter, self, value, nullptr);
    Py_DECREF(value);
    JS_PROPAGATE_PY(result);
    Py_DECREF(result);
    if (instance && binding->replaceable) {
        const char* name = PyUnicode_AsUTF8_abi3(binding->name);
        if (name == nullptr) { js_throw_py(); return; }
        auto replaced = info.This()->CreateDataProperty(context, V8String(isolate, name), info[0]);
        if (replaced.IsNothing()) return;
    }
}

static void attribute_set(const FunctionCallbackInfo<Value>& info, bool instance) {
    SETUP;
    if (!context_check_callback(context)) return;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (!context_trace_enabled(owner)) { attribute_set_impl(info, instance); return; }
    auto* binding = (AttributeSpec*)IsolateState::callback_target(info.Data());
    if (binding == nullptr) { js_throw_py(); return; }
    PyObject* receiver = binding->setter_receiver ? get_self(info) : Py_None;
    faker::TraceScope observation(info, 1, binding->name, receiver, owner);
    attribute_set_impl(info, instance);
}

void py_attribute_for_instance_set(const FunctionCallbackInfo<Value>& info) { attribute_set(info, true); }
void py_attribute_for_property_set(const FunctionCallbackInfo<Value>& info) { attribute_set(info, false); }

PyObject* py_class_handler(PyObject* receiver, const char* name, context_c* context)
{
    if (receiver == nullptr) { PyErr_SetString(PyExc_RuntimeError, "callback receiver is unavailable"); return nullptr; }
    PyObject* cached = context == nullptr ? nullptr : PyDict_GetItemWithError(context->class_template_cache, (PyObject*)Py_TYPE(receiver));
    if (cached != nullptr && ((py_class*)cached)->idl_spec != nullptr) {
        const auto& spec = *(IdlSpec*)((py_class*)cached)->idl_spec;
        PyObject* callback = nullptr;
        bool recognized = true;
        if (std::strcmp(name, "__v8_name_get__") == 0) callback = spec.name_get;
        else if (std::strcmp(name, "__v8_name_set__") == 0) callback = spec.name_set;
        else if (std::strcmp(name, "__v8_name_query__") == 0) callback = spec.name_query;
        else if (std::strcmp(name, "__v8_name_enum__") == 0) callback = spec.name_enum;
        else if (std::strcmp(name, "__v8_name_del__") == 0) callback = spec.name_del;
        else if (std::strcmp(name, "__v8_index_get__") == 0) callback = spec.index_get;
        else if (std::strcmp(name, "__v8_index_set__") == 0) callback = spec.index_set;
        else if (std::strcmp(name, "__v8_index_query__") == 0) callback = spec.index_query;
        else if (std::strcmp(name, "__v8_index_enum__") == 0) callback = spec.index_enum;
        else if (std::strcmp(name, "__v8_index_del__") == 0) callback = spec.index_del;
        else if (std::strcmp(name, "__v8_cross_origin_check__") == 0) callback = spec.cross_origin_check;
        else if (std::strcmp(name, "__v8_cross_origin_name_get__") == 0) callback = spec.cross_origin_name_get;
        else if (std::strcmp(name, "__v8_cross_origin_name_set__") == 0) callback = spec.cross_origin_name_set;
        else if (std::strcmp(name, "__v8_cross_origin_name_query__") == 0) callback = spec.cross_origin_name_query;
        else if (std::strcmp(name, "__v8_cross_origin_name_enum__") == 0) callback = spec.cross_origin_name_enum;
        else if (std::strcmp(name, "__v8_cross_origin_name_del__") == 0) callback = spec.cross_origin_name_del;
        else if (std::strcmp(name, "__v8_cross_origin_index_get__") == 0) callback = spec.cross_origin_index_get;
        else if (std::strcmp(name, "__v8_cross_origin_index_set__") == 0) callback = spec.cross_origin_index_set;
        else if (std::strcmp(name, "__v8_cross_origin_index_query__") == 0) callback = spec.cross_origin_index_query;
        else if (std::strcmp(name, "__v8_cross_origin_index_enum__") == 0) callback = spec.cross_origin_index_enum;
        else if (std::strcmp(name, "__v8_cross_origin_index_del__") == 0) callback = spec.cross_origin_index_del;
        else recognized = false;
        if (recognized) {
            if (callback == nullptr) return nullptr;
            if (PyMethod_Check_abi3(callback)) { Py_INCREF(callback); return callback; }
            return PyMethod_New_abi3(callback, receiver);
        }
    }
    PyObject* method = PyObject_GetAttrString(receiver, name);
    if (method == nullptr && PyErr_ExceptionMatches(PyExc_AttributeError)) PyErr_Clear();
    return method;
}

static PyObject* get_handler_method(PyObject* self, const char* name)
{
    auto* isolate = v8::Isolate::GetCurrent();
    auto current = isolate->GetCurrentContext();
    auto* context = v8_compat::context_embedder_pointer<context_c>(current, CONTEXT_OBJECT_SLOT);
    return py_class_handler(self, name, context);
}

Intercepted property_getter_handler(
    PyObject* key, const PropertyCallbackInfo<Value>& info,
    Local<Context> context, PyObject* self, const char* method_name,
    Local<Name> js_name, bool report_declined);

// Legacy platform objects report failed writes/deletes through the V8
// callback result.  Python callbacks cannot see ShouldThrowOnError(), so a
// marked exception is suppressed in sloppy mode and propagated in strict
// mode by the native bridge.
static bool
consume_legacy_property_error(bool should_throw)
{
    if (!PyErr_Occurred())
        return false;
    PyObject *exc_type = NULL, *exc_value = NULL, *exc_traceback = NULL;
    PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);
    PyErr_NormalizeException(&exc_type, &exc_value, &exc_traceback);
    int marked = exc_value != NULL && PyObject_HasAttrString(
        exc_value, "__v8_interceptor_failure__");
    // fprintf(stderr, "legacy marked=%d strict=%d\\n", marked, should_throw);
    if (marked && !should_throw) {
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(exc_traceback);
        return true;
    }
    PyErr_Restore(exc_type, exc_value, exc_traceback);
    return false;
}

static bool
property_query_value(PyObject* key, PyObject* self, const char* method_name,
                     long* attributes)
{
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        if (!PyErr_Occurred())
            return false;
        return false;
    }
    PyObject* result = PyObject_CallFunctionObjArgs(method, key, NULL);
    Py_DECREF(method);
    if (result == NULL)
        return false;
    if (result == Py_None) {
        Py_DECREF(result);
        return false;
    }
    if (!PyLong_Check(result)) {
        Py_DECREF(result);
        return false;
    }
    *attributes = PyLong_AsLong(result);
    Py_DECREF(result);
    return !PyErr_Occurred();
}

Intercepted property_getter_handler_impl(PyObject* key,
                                        const PropertyCallbackInfo<Value>& info,
                                        Local<Context> context,
                                        PyObject* self,
                                        const char* method_name);

static Intercepted
property_descriptor(PyObject* key, const PropertyCallbackInfo<Value>& info,
                    Local<Context> context, PyObject* self,
                    const char* method_name, bool enumerable)
{
    // A descriptor is not a property read: resolving it through the traced
    // getter reports an extra event for a single read, because the engine asks
    // for the descriptor *and* performs the read (``typeof``, ``in`` and
    // ``getOwnPropertyDescriptor`` all do).
    Intercepted intercepted = property_getter_handler_impl(
        key, info, context, self, method_name);
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
                        const char* method_name)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        Py_DECREF(key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* result = PyObject_CallFunctionObjArgs(method, key, NULL);
    Py_DECREF(method);
    Py_DECREF(key);
    if (result == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (result == Py_None) {
        Py_DECREF(result);
        return Intercepted::kNo;
    }
    Local<Value> js_result = js_from_py(result, context);
    Py_DECREF(result);
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
                        const PropertyCallbackInfo<void>& info,
                        Local<Context> context,
                        PyObject* self,
                        const char* method_name)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* value = py_from_js(js_value, context);
    if (value == NULL) {
        Py_DECREF(key);
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        Py_DECREF(key);
        Py_DECREF(value);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* result = PyObject_CallFunctionObjArgs(method, key, value, NULL);
    Py_DECREF(method);
    Py_DECREF(key);
    Py_DECREF(value);
    if (result == NULL) {
        if (consume_legacy_property_error(info.ShouldThrowOnError()))
            return Intercepted::kYes;
        js_throw_py();
        return Intercepted::kNo;
    }
    if (result == Py_None) {
        Py_DECREF(result);
        return Intercepted::kNo;
    }
    Py_DECREF(result);
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
        Py_DECREF(item);
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
        Py_DECREF(item);
        if (matches) return true;
    }
    return false;
}

// Does a real property provide ``name`` on the holder or its prototypes?  The
// walk skips interceptors and only asks the engine what it stores, so it never
// runs JavaScript and is safe to use while reporting a probe.
template <class Info>
static bool real_property_exists(const PropertyCallbackInfo<Info>& info,
                                 Local<Name> js_name, Local<Context> context) {
    if (js_name.IsEmpty()) return false;
    Local<Object> object = v8_compat::callback_holder(info);
    while (!object.IsEmpty()) {
        if (object->HasRealNamedProperty(context, js_name).FromMaybe(false)) return true;
        Local<Value> prototype = object->GetPrototype();
        object = prototype->IsObject() ? prototype.As<Object>() : Local<Object>();
    }
    return false;
}

// Does the holder itself store a real property for ``name``?  A descriptor probe
// answers about own properties, which is what ``Object.getOwnPropertyDescriptor``
// and ``hasOwnProperty`` ask for, while ``in`` walks the whole prototype chain.
template <class Info>
static bool own_real_property_exists(const PropertyCallbackInfo<Info>& info,
                                     Local<Name> js_name, Local<Context> context) {
    if (js_name.IsEmpty()) return false;
    return info.Holder()->HasRealNamedProperty(context, js_name).FromMaybe(false);
}

// Does the receiver's interface declare this name as one of its own members?
// Declared attributes and operations observe themselves, through the accessor or
// the call the binding installs, so reporting them here as well would log every
// declared property access twice.  Only names the interface does not declare are
// reported by the interceptor: native globals such as ``eval``, JavaScript
// expandos and properties inherited from plain JavaScript objects.
static bool declares_member(PyObject* receiver, context_c* context, PyObject* name, bool setter) {
    if (receiver == nullptr || receiver == Py_None || context == nullptr || name == nullptr) return false;
    if (!PyUnicode_Check(name)) return false;
    PyObject* cached = PyDict_GetItemWithError(context->class_template_cache, (PyObject*)Py_TYPE(receiver));
    if (cached == nullptr || ((py_class*)cached)->idl_spec == nullptr) {
        PyErr_Clear();
        return false;
    }
    const auto& spec = *(IdlSpec*)((py_class*)cached)->idl_spec;
    if (declares_attribute(spec.attributes, name, setter)) return true;
    return !setter && declares_operation(spec.methods, name);
}

// The value the engine produces for a name this interceptor declined, or an
// empty handle when producing it would require running JavaScript.
//
// Only a real data property is read.  Resolving an accessor here would run the
// getter the engine is about to run a second time, so an accessor and a missing
// name both yield an empty handle: those reads stay unreported instead of being
// reported with a value this layer never obtained.
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
                                    Local<Context> context, PyObject* self, const char* method_name,
                                    Local<Name> js_name, bool report_declined) {
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (!context_trace_enabled(owner)) return property_getter_handler_impl(key, info, context, self, method_name);
    Py_XINCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_getter_handler_impl(key, info, context, self, method_name);
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
    Py_XDECREF(key);
    return result;
}

Intercepted property_setter_handler(PyObject* key, Local<Value> value, const PropertyCallbackInfo<void>& info,
                                    Local<Context> context, PyObject* self, const char* method_name,
                                    bool report_declined) {
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    if (!context_trace_enabled(owner)) return property_setter_handler_impl(key, value, info, context, self, method_name);
    Py_XINCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_setter_handler_impl(key, value, info, context, self, method_name);
    if (result == Intercepted::kYes || observation.failed()) {
        observation.emit(1, key, value);
    } else if (report_declined && self != Py_None && !declares_member(self, owner, key, true)) {
        // A declined write lands on a JavaScript property (an expando or a
        // JavaScript accessor) or is dropped by the engine; the assigned value is
        // already at hand, so it is reported as is.
        observation.emit(1, key, value);
    }
    Py_XDECREF(key);
    return result;
}

Intercepted
property_deleter_handler(PyObject* key,
                         const PropertyCallbackInfo<Boolean>& info,
                         PyObject* self,
                         const char* method_name)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        Py_DECREF(key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* result = PyObject_CallFunctionObjArgs(method, key, NULL);
    Py_DECREF(method);
    Py_DECREF(key);
    if (result == NULL) {
        if (consume_legacy_property_error(info.ShouldThrowOnError())) {
            info.GetReturnValue().Set(false);
            return Intercepted::kYes;
        }
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!PyBool_Check(result)) {
        Py_DECREF(result);
        return Intercepted::kNo;
    }
    info.GetReturnValue().Set(Boolean::New(info.GetIsolate(), result == Py_True));
    Py_DECREF(result);
    return Intercepted::kYes;
}

Intercepted
property_query_handler(PyObject* key,
                       const PropertyCallbackInfo<Integer>& info,
                       PyObject* self,
                       const char* method_name)
{
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        Py_DECREF(key);
        if (PyErr_Occurred())
            js_throw_py();
        return Intercepted::kNo;
    }
    PyObject* result = PyObject_CallFunctionObjArgs(method, key, NULL);
    Py_DECREF(method);
    Py_DECREF(key);
    if (result == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!PyLong_Check(result)) {
        Py_DECREF(result);
        return Intercepted::kNo;
    }
    long attributes = PyLong_AsLong(result);
    Py_DECREF(result);
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
                         const char* method_name)
{
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        if (PyErr_Occurred())
            js_throw_py();
        return;
    }
    PyObject* keys = PyObject_CallNoArgs(method);
    Py_DECREF(method);
    if (keys == NULL) {
        js_throw_py();
        return;
    }
    if (keys == Py_None || !PySequence_Check(keys)) {
        Py_DECREF(keys);
        return;
    }
    Py_ssize_t length = PySequence_Length(keys);
    if (length < 0 || length > INT_MAX) {
        Py_DECREF(keys);
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_OverflowError, "handler key sequence is too large");
        js_throw_py();
        return;
    }
    Local<Array> js_keys = Array::New(info.GetIsolate(), (int)length);
    for (Py_ssize_t i = 0; i < length; ++i) {
        PyObject* item = PySequence_GetItem(keys, i);
        if (item == NULL) {
            Py_DECREF(keys);
            js_throw_py();
            return;
        }
        Local<Value> js_item = js_from_py(item, context);
        Py_DECREF(item);
        if (js_item.IsEmpty() || PyErr_Occurred()) {
            Py_DECREF(keys);
            js_throw_py();
            return;
        }
        bool set_worked;
        if (!js_keys->Set(context, (uint32_t)i, js_item).To(&set_worked)) {
            Py_DECREF(keys);
            PyErr_SetString(PyExc_RuntimeError, "failed to set handler enumeration key");
            js_throw_py();
            return;
        }
    }
    Py_DECREF(keys);
    info.GetReturnValue().Set(js_keys);
}

void
indexed_enumerator_handler(const PropertyCallbackInfo<Array>& info,
                           Local<Context> context,
                           PyObject* self,
                           const char* method_name)
{
    PyObject* method = get_handler_method(self, method_name);
    if (method == NULL) {
        if (PyErr_Occurred())
            js_throw_py();
        return;
    }
    PyObject* py_length = PyObject_CallNoArgs(method);
    Py_DECREF(method);
    if (py_length == NULL) {
        js_throw_py();
        return;
    }
    if (py_length == Py_None || !PyLong_Check(py_length)) {
        Py_DECREF(py_length);
        return;
    }
    long length = PyLong_AsLong(py_length);
    Py_DECREF(py_length);
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


Intercepted named_getter(Local<Name> js_name, Info(Value)) {
    SETUP
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_name_get__", js_name);
}

// Named-properties objects belong to their creation realm, not whichever
// realm happens to invoke a borrowed prototype. Bypass interceptors when
// checking ordinary properties to avoid recursion through this handler.
template <class T>
static PyObject* properties_owner(const PropertyCallbackInfo<T>& info, Local<Name> name) {
    Local<Context> owner;
    Local<Object> holder = v8_compat::callback_holder(info);
    if (!holder->GetCreationContext().ToLocal(&owner)) return Py_None;
    auto* ctx = v8_compat::context_embedder_pointer<context_c>(owner, CONTEXT_OBJECT_SLOT);
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
        "__v8_property_name_get__", js_name, false);
}

Intercepted properties_named_query(Local<Name> name, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    return property_query_handler(py_from_js(name, context), info,
        properties_owner(info, name), "__v8_property_name_query__");
}

Intercepted properties_named_descriptor(Local<Name> name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto result = property_getter_handler_impl(py_from_js(name, context), info, context,
        properties_owner(info, name), "__v8_property_name_get__");
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

Intercepted properties_indexed_getter(uint32_t index, Info(Value)) {
    return properties_named_getter(V8String(info.GetIsolate(), std::to_string(index).c_str()), info);
}
Intercepted properties_indexed_query(uint32_t index, Info(Integer)) {
    return properties_named_query(V8String(info.GetIsolate(), std::to_string(index).c_str()), info);
}
Intercepted properties_indexed_descriptor(uint32_t index, Info(Value)) {
    return properties_named_descriptor(V8String(info.GetIsolate(), std::to_string(index).c_str()), info);
}
Intercepted properties_indexed_deleter(uint32_t index, Info(Boolean)) {
    return properties_named_deleter(V8String(info.GetIsolate(), std::to_string(index).c_str()), info);
}
Intercepted properties_indexed_definer(uint32_t index, const PropertyDescriptor& desc, Info(Boolean)) {
    return properties_named_definer(V8String(info.GetIsolate(), std::to_string(index).c_str()), desc, info);
}


Intercepted named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_name_set__");
}

Intercepted named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    PyObject* self = get_self(info);
    return property_deleter_handler(py_from_js(js_name, context), info, self, "__v8_name_del__");
}

Intercepted named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    PyObject* self = get_self(info);
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_query_handler(py_from_js(js_name, context), info, self, "__v8_name_query__");
    PyObject* key = py_from_js(js_name, context);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    Py_INCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_query_handler(key, info, self, "__v8_name_query__");
    // ``'name' in target`` is an existence probe, not a read: it is reported as
    // its own event carrying the answer the engine reaches for this name.
    observation.emit(4, key, Boolean::New(isolate, result == Intercepted::kYes ||
                                                    real_property_exists(info, js_name, context)));
    Py_DECREF(key);
    return result;
}

void named_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;
    PyObject* self = get_self(info);
    named_enumerator_handler(info, context, self, "__v8_name_enum__");
}

Intercepted indexed_getter(uint32_t index, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_index_get__");
}

Intercepted indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);


    return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_index_set__");
}

Intercepted indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    PyObject* self = get_self(info);
    return property_deleter_handler(PyLong_FromSize_t(index), info, self, "__v8_index_del__");
}

Intercepted indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    PyObject* self = get_self(info);
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_query_handler(PyLong_FromSize_t(index), info, self, "__v8_index_query__");
    PyObject* key = PyLong_FromSize_t(index);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    Py_INCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_query_handler(key, info, self, "__v8_index_query__");
    const bool exists = v8_compat::callback_holder(info)
        ->HasRealIndexedProperty(context, index).FromMaybe(false);
    observation.emit(4, key, Boolean::New(isolate, result == Intercepted::kYes || exists));
    Py_DECREF(key);
    return result;
}

void indexed_enumerator(Info(Array)) {
    SETUP;
    if (!context_check_callback(context)) return;
    PyObject* self = get_self(info);
    indexed_enumerator_handler(info, context, self, "__v8_index_enum__");
}

Intercepted named_descriptor(Local<Name> js_name, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
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
            faker::TraceEvent observation(info.GetIsolate(), owner, self);
            observation.emit(5, key, Boolean::New(isolate, own_real_property_exists(info, js_name, context)));
            Py_DECREF(key);
        }
        return Intercepted::kNo;
    }
    PyObject* key = py_from_js(js_name, context);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!report) return property_descriptor(key, info, context, self, "__v8_name_get__", false);
    Py_INCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_descriptor(key, info, context, self, "__v8_name_get__", false);
    // A descriptor probe asks what the own property is, not what it holds, so it
    // is reported as its own event and never as a getter event.
    observation.emit(5, key, Boolean::New(isolate, result == Intercepted::kYes ||
                                                    own_real_property_exists(info, js_name, context)));
    Py_DECREF(key);
    return result;
}

Intercepted indexed_descriptor(uint32_t index, Info(Value)) {
    SETUP;
    if (!context_check_callback(context)) return Intercepted::kYes;
    auto* owner = v8_compat::context_embedder_pointer<context_c>(context, CONTEXT_OBJECT_SLOT);
    PyObject* self = get_self(info);
    PyObject* key = PyLong_FromSize_t(index);
    if (key == NULL) {
        js_throw_py();
        return Intercepted::kNo;
    }
    if (!context_trace_enabled(owner) || self == Py_None)
        return property_descriptor(key, info, context, self, "__v8_index_get__", true);
    Py_INCREF(key);
    faker::TraceEvent observation(info.GetIsolate(), owner, self);
    auto result = property_descriptor(key, info, context, self, "__v8_index_get__", true);
    const bool exists = v8_compat::callback_holder(info)
        ->HasRealIndexedProperty(context, index).FromMaybe(false);
    observation.emit(5, key, Boolean::New(isolate, result == Intercepted::kYes || exists));
    Py_DECREF(key);
    return result;
}

static Intercepted
property_definer(PyObject* key, const PropertyDescriptor& desc,
                 const PropertyCallbackInfo<Boolean>& info,
                 PyObject* self, const char* query_method,
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
        key, desc, info, self, "__v8_name_query__",
        "Named property setter is not supported.");
    Py_DECREF(key);
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
        key, desc, info, self, "__v8_index_query__",
        "Indexed property setter is not supported.");
    Py_DECREF(key);
    return result;
}
