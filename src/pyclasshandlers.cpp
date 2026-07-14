#include <v8.h>
#include <Python.h>
#include <climits>
#include <sstream>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "faker/hook.h"

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



template <class Info>
static PyObject*
get_handler_self(const Info& info, bool cross_origin)
{
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
    if (magic != runtime_state.v8_string(V8StringId::PythonObjectMagic))
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

#define Info(T) const PropertyCallbackInfo<T> &info


static void
py_class_construct_impl(const FunctionCallbackInfo<Value>& info,
                        Local<Context> context,
                        py_class* self,
                        context_c* py_context)
{
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
    PyObject* new_object = PyObject_Call(self->cls, args, kwargs);
    Py_DECREF(args);
    Py_DECREF(kwargs);
    if (new_object == NULL) {
        js_throw_py();
        return;
    }
    if (!py_class_init_js_object(info.Holder(), new_object, context)) {
        Py_DECREF(new_object);
        js_throw_py();
        return;
    }
    Py_DECREF(new_object);
}


void py_class_construct_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;

    py_class *self = (py_class *) info.Data().As<External>()->Value();
    context_c* ctx_c = (context_c*)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (ctx_c->hook) {
        faker::FakerHookConstruct construct_hook(info, PyUnicode_AsUTF8_abi3(self->cls_name),  self->cls, ctx_c);
        py_class_construct_impl(info, context, self, ctx_c);
    }
    else {
        py_class_construct_impl(info, context, self, ctx_c);
    }
}

static void
py_class_method_impl(const FunctionCallbackInfo<Value>& info,
                     Local<Context> context,
                     PyObject* self,
                     PyObject* method,
                     bool cross_origin)
{
    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    if (method_name == NULL || !PyUnicode_Check(method_name)) {
        Py_XDECREF(method_name);
        PyErr_SetString(PyExc_TypeError, "Python callback must have a string __name__");
        js_throw_py();
        return;
    }
    PyObject* callable;
    if (self == Py_None) {
        Py_INCREF(method);
        callable = method;
    }
    else {
        callable = PyObject_GetAttr(self, method_name);
    }
    if (callable == NULL) {
        Py_DECREF(method_name);
        js_throw_py();
        return;
    }

    PyObject* args = pys_from_jss(info, context);
    if (args == NULL) {
        Py_DECREF(callable);
        Py_DECREF(method_name);
        js_throw_py();
        return;
    }
    PyObject* kwargs = NULL;
    if (cross_origin) {
        Local<Context> incumbent = isolate->GetIncumbentContext();
        if (incumbent.IsEmpty()) {
            Py_DECREF(args);
            Py_DECREF(callable);
            Py_DECREF(method_name);
            PyErr_SetString(PyExc_RuntimeError, "cross-origin callback has no incumbent context");
            js_throw_py();
            return;
        }
        context_c* incumbent_ctx = (context_c*)incumbent->GetEmbedderData(CONTEXT_OBJECT_SLOT)
            .As<External>()->Value();
        kwargs = PyDict_New();
        if (kwargs == NULL || PyDict_SetItemString(kwargs, "win", incumbent_ctx->py_global) < 0) {
            Py_XDECREF(kwargs);
            Py_DECREF(args);
            Py_DECREF(callable);
            Py_DECREF(method_name);
            js_throw_py();
            return;
        }
    }

    PyObject* result = PyObject_Call(callable, args, kwargs);
    Py_XDECREF(kwargs);
    Py_DECREF(args);
    Py_DECREF(callable);
    Py_DECREF(method_name);
    if (result == NULL) {
        js_throw_py();
        return;
    }
    Local<Value> js_result = js_from_py(result, context);
    Py_DECREF(result);
    if (js_result.IsEmpty() || PyErr_Occurred()) {
        js_throw_py();
        return;
    }
    info.GetReturnValue().Set(js_result);
}


void py_class_cross_origin_method_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;

    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }
    //Local<Context> incumbent_ctx = isolate->GetIncumbentContext();

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    const char* method_name_str;
    if (method_name && PyUnicode_Check(method_name)) {
        method_name_str = PyUnicode_AsUTF8_abi3(method_name);
    }
    else {
        method_name_str = "<unknown>";
    }
    Py_XDECREF(method_name);

    context_c* ctx_c = (context_c*)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (ctx_c->hook) {
        faker::FakerHookOperation operation_hook(info, method_name_str, self, ctx_c);
        py_class_method_impl(info, context, self, method, true);
    }
    else {
        py_class_method_impl(info, context, self, method, true);
    }
}

void py_class_method_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    
    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }
    //Local<Context> incumbent_ctx = isolate->GetIncumbentContext();

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    const char* method_name_str;
    if (method_name && PyUnicode_Check(method_name)) {
        method_name_str = PyUnicode_AsUTF8_abi3(method_name);
    }
    else {
        method_name_str = "<unknown>";
    }
    Py_XDECREF(method_name);

    context_c* ctx_c = (context_c*)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (ctx_c->hook) {
        faker::FakerHookOperation operation_hook(info, method_name_str, self, ctx_c);
        py_class_method_impl(info, context, self, method, false);
    }
    else {
        py_class_method_impl(info, context, self, method, false);
    }
}

// --- Interceptors ---                                                      
void py_class_property_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

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

    PyObject* self = get_self(info);
    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

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

void py_attribute_for_get(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
        PyObject* result = PyObject_CallFunction(method, "", NULL);
        JS_PROPAGATE_PY(result);
        info.GetReturnValue().Set(js_from_py(result, context));
        Py_XDECREF(result);
        return;
    }

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    PyObject* result = PyObject_CallMethodNoArgs_abi3(self, method_name);
    Py_XDECREF(method_name);

    JS_PROPAGATE_PY(result);

    info.GetReturnValue().Set(js_from_py(result, context));
    Py_XDECREF(result);
}

void py_attribute_for_instance_set(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    if (info.Length() < 1) {
        info.GetIsolate()->ThrowException(v8::Exception::TypeError(V8String(info.GetIsolate(), "1 argument required, but only 0 present")));
        return;
    }
    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }

    PyObject* value = py_from_js(info[0], context);
    JS_PROPAGATE_PY(value);

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    PyObject* result = PyObject_CallMethodOneArg_abi3(self, method_name, value);

    Py_XDECREF(value);
    Py_XDECREF(method_name);
    Py_XDECREF(result);
    JS_PROPAGATE_PY(result);

    PyObject* raw_name = PyObject_GetAttrString(method, "__v8_raw_name__");
    const char* js_name = PyUnicode_AsUTF8_abi3(raw_name);
    Py_XDECREF(raw_name);

    // [Replaceable]
    bool did_create;
    v8::Local<v8::Object> v8_receiver = info.This();

    if (!v8_receiver->SetAccessor(context, V8String(isolate, js_name), py_class_property_getter, py_class_property_setter, info.Data()).To(&did_create)) {
        return;
    }
}

void py_attribute_for_property_set(const FunctionCallbackInfo<Value>& info) {
    SETUP;

    if (info.Length() < 1) {
        info.GetIsolate()->ThrowException(v8::Exception::TypeError(V8String(info.GetIsolate(), "1 argument required, but only 0 present")));
        return;
    }

    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)info.Data().As<External>()->Value();

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }

    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    PyObject* value = py_from_js(info[0], context);
    JS_PROPAGATE_PY(value);

    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    PyObject* result = PyObject_CallMethodOneArg_abi3(self, method_name, value);
    Py_XDECREF(value);
    Py_XDECREF(method_name);
    Py_XDECREF(result);
    JS_PROPAGATE_PY(result);
}

static PyObject*
get_handler_method(PyObject* self, const char* method_name)
{
    PyObject* method = PyObject_GetAttrString(self, method_name);
    if (method == NULL && PyErr_ExceptionMatches(PyExc_AttributeError))
        PyErr_Clear();
    return method;
}

static Intercepted
property_getter_handler(PyObject* key,
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

static Intercepted
property_setter_handler(PyObject* key,
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

static Intercepted
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

static Intercepted
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

static void
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

static void
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

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookGetter hook(info, js_name, self, ctx_c);
        return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_name_get__");
    }
    else
    {
        return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_name_get__");
    }
}

Intercepted properties_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = ctx_c->py_global;

    return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_property_name_get__");
}

Intercepted cross_origin_named_getter(Local<Name> js_name, Info(Value)){
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_cross_origin_name_get__");

}



Intercepted named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);
    
    if (ctx_c->hook) {
        faker::FakerHookSetter hook(info, js_name, js_value, self, ctx_c);
        return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_name_set__");
    }
    else {
        return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_name_set__");
    }
}

Intercepted cross_origin_named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_cross_origin_name_set__");
}

Intercepted named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_deleter_handler(py_from_js(js_name, context), info, self, "__v8_name_del__");
}

Intercepted cross_origin_named_deleter(Local<Name> js_name, Info(Boolean)){
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(py_from_js(js_name, context), info, self, "__v8_cross_origin_name_del__");
}

Intercepted named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_query_handler(py_from_js(js_name, context), info, self, "__v8_name_query__");
}

Intercepted cross_origin_named_query(Local<Name> js_name, Info(Integer)){
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(py_from_js(js_name, context), info, self, "__v8_cross_origin_name_query__");
}

void named_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    named_enumerator_handler(info, context, self, "__v8_name_enum__");
}

void cross_origin_named_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    named_enumerator_handler(info, context, self, "__v8_cross_origin_name_enum__");
}

Intercepted indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookGetter hook(info, index, self, ctx_c);
        return property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_index_get__");
    }
    else {
        return property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_index_get__");
    }
}

Intercepted cross_origin_indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    return property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_cross_origin_index_get__");
}


Intercepted indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookSetter hook(info, index, js_value, self, ctx_c);
        return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_index_set__");
    }
    else {
        return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_index_set__");
    }
}

Intercepted cross_origin_indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_cross_origin_index_set__");
}

Intercepted indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_deleter_handler(PyLong_FromSize_t(index), info, self, "__v8_index_del__");
}

Intercepted cross_origin_indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    return property_deleter_handler(PyLong_FromSize_t(index), info, self, "__v8_cross_origin_index_del__");
}

Intercepted indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_query_handler(PyLong_FromSize_t(index), info, self, "__v8_index_query__");
}

Intercepted cross_origin_indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    return property_query_handler(PyLong_FromSize_t(index), info, self, "__v8_cross_origin_index_query__");
}

void indexed_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    indexed_enumerator_handler(info, context, self, "__v8_index_enum__");
}

void cross_origin_indexed_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    indexed_enumerator_handler(info, context, self, "__v8_cross_origin_index_enum__");
}


bool cross_origin_access_check(v8::Local<v8::Context> accessing_context, v8::Local<v8::Object> accessed_object, v8::Local<v8::Value> unused_data) {
    context_c* accessing_ctx_c = (context_c*)accessing_context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* accessor = accessing_ctx_c->py_global;

    Local<Context> accessed_context;
    if (!accessed_object->GetCreationContext().ToLocal(&accessed_context)) {
        return false;
    }
    context_c* interviewee_ctx_c = (context_c*)accessed_context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();

    PyObject* interviewee = interviewee_ctx_c->py_global;
    if (PyObject_HasAttrString(interviewee, "__v8_cross_origin_check__")) {
        Py_INCREF(accessor);
        PyObject* value = PyObject_CallMethod(interviewee, "__v8_cross_origin_check__", "O", accessor);
        if (value == Py_False) {
            return false;
        }
        Py_XDECREF(value);
    }
    return true;
}
