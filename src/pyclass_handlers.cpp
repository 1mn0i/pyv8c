#include <v8.h>
#include <Python.h>
#include <climits>
#include <sstream>

#include "pyv8.h"
#include "convert.h"
#include "pyclass.h"
#include "pyclass_handlers_internal.h"
#include "faker/hook.h"

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


static bool
is_direct_callback(PyObject* method, bool* result)
{
    int has_marker = PyObject_HasAttrString(method, "__v8_direct_callback__");
    if (has_marker < 0)
        return false;
    if (has_marker == 0) {
        *result = false;
        return true;
    }
    PyObject* marker = PyObject_GetAttrString(method, "__v8_direct_callback__");
    if (marker == NULL)
        return false;
    int truth = PyObject_IsTrue(marker);
    Py_DECREF(marker);
    if (truth < 0)
        return false;
    *result = truth != 0;
    return true;
}


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

    py_class *self = (py_class *) v8_compat::external_value(info.Data());
    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return;
    if (ctx_c->hook) {
        {
            faker::FakerHookConstruct construct_hook(info, PyUnicode_AsUTF8_abi3(self->cls_name), self->cls, ctx_c);
            py_class_construct_impl(info, context, self, ctx_c);
        }
        if (PyErr_Occurred())
            js_throw_py();
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
    Isolate* isolate = info.GetIsolate();
    PyObject* method_name = PyObject_GetAttrString(method, "__name__");
    if (method_name == NULL || !PyUnicode_Check(method_name)) {
        Py_XDECREF(method_name);
        PyErr_SetString(PyExc_TypeError, "Python callback must have a string __name__");
        js_throw_py();
        return;
    }
    bool direct_callback = false;
    if (!is_direct_callback(method, &direct_callback)) {
        js_throw_py();
        return;
    }
    PyObject* callable;
    if (direct_callback || self == Py_None) {
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
    if (direct_callback && self != Py_None) {
        Py_ssize_t length = PyTuple_Size(args);
        PyObject* with_self = PyTuple_New(length + 1);
        if (with_self == NULL) {
            Py_DECREF(args);
            Py_DECREF(callable);
            Py_DECREF(method_name);
            js_throw_py();
            return;
        }
        Py_INCREF(self);
        if (PyTuple_SetItem(with_self, 0, self) < 0) {
            Py_DECREF(with_self);
            Py_DECREF(args);
            Py_DECREF(callable);
            Py_DECREF(method_name);
            js_throw_py();
            return;
        }
        for (Py_ssize_t index = 0; index < length; ++index) {
            PyObject* value = PyTuple_GetItem(args, index);
            Py_INCREF(value);
            if (PyTuple_SetItem(with_self, index + 1, value) < 0) {
                Py_DECREF(value);
                Py_DECREF(with_self);
                Py_DECREF(args);
                Py_DECREF(callable);
                Py_DECREF(method_name);
                js_throw_py();
                return;
            }
        }
        Py_DECREF(args);
        args = with_self;
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
        context_c* incumbent_ctx =
            v8_compat::context_embedder_pointer<context_c>(
                incumbent, CONTEXT_OBJECT_SLOT);
        if (incumbent_ctx == nullptr) {
            Py_DECREF(args);
            Py_DECREF(callable);
            Py_DECREF(method_name);
            PyErr_SetString(PyExc_RuntimeError,
                            "cross-origin callback has no Python context");
            js_throw_py();
            return;
        }
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

static bool
get_method_callback_data(const FunctionCallbackInfo<Value>& info,
                         PyObject** method,
                         std::string* js_name)
{
    Local<Value> data = info.Data();
    if (!data->IsExternal()) {
        PyErr_SetString(PyExc_RuntimeError, "invalid Python method callback data");
        js_throw_py();
        return false;
    }

    PyObject* callback_data =
        (PyObject*)v8_compat::external_value(data);
    PyObject* name = NULL;
    bool owns_name = false;
    if (PyTuple_Check(callback_data) && PyTuple_Size(callback_data) == 2) {
        *method = PyTuple_GetItem(callback_data, 0);
        name = PyTuple_GetItem(callback_data, 1);
    }
    else {
        *method = callback_data;
        name = PyObject_GetAttrString(*method, "__name__");
        owns_name = true;
    }

    if (name == NULL || !PyUnicode_Check(name)) {
        if (owns_name)
            Py_XDECREF(name);
        PyErr_SetString(PyExc_TypeError, "Python callback must have a string name");
        js_throw_py();
        return false;
    }
    const char* name_utf8 = PyUnicode_AsUTF8_abi3(name);
    if (name_utf8 != NULL)
        *js_name = name_utf8;
    if (owns_name)
        Py_DECREF(name);
    if (name_utf8 == NULL) {
        js_throw_py();
        return false;
    }
    return true;
}


void py_class_cross_origin_method_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;

    PyObject* self = get_self(info);

    PyObject* method;
    std::string js_method_name;
    if (!get_method_callback_data(info, &method, &js_method_name))
        return;
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }
    //Local<Context> incumbent_ctx = isolate->GetIncumbentContext();

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return;
    if (ctx_c->hook) {
        {
            faker::FakerHookOperation operation_hook(info, js_method_name.c_str(), self, ctx_c);
            py_class_method_impl(info, context, self, method, true);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else {
        py_class_method_impl(info, context, self, method, true);
    }
}

void py_class_method_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;
    
    PyObject* self = get_self(info);

    PyObject* method;
    std::string js_method_name;
    if (!get_method_callback_data(info, &method, &js_method_name))
        return;
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }
    //Local<Context> incumbent_ctx = isolate->GetIncumbentContext();

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return;
    if (ctx_c->hook) {
        {
            faker::FakerHookOperation operation_hook(info, js_method_name.c_str(), self, ctx_c);
            py_class_method_impl(info, context, self, method, false);
        }
        if (PyErr_Occurred())
            js_throw_py();
    }
    else {
        py_class_method_impl(info, context, self, method, false);
    }
}

// --- Interceptors ---                                                      
void py_class_property_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    PyObject* self = get_self(info);

    PyObject* method = (PyObject*)v8_compat::external_value(info.Data());
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
    PyObject* method = (PyObject*)v8_compat::external_value(info.Data());
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

    PyObject* method = (PyObject*)v8_compat::external_value(info.Data());
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    bool direct_callback = false;
    if (!is_direct_callback(method, &direct_callback)) {
        js_throw_py();
        return;
    }
    if (direct_callback) {
        PyObject* result = self == Py_None ? PyObject_CallNoArgs(method) :
            PyObject_CallFunctionObjArgs(method, self, NULL);
        JS_PROPAGATE_PY(result);
        info.GetReturnValue().Set(js_from_py(result, context));
        Py_XDECREF(result);
        return;
    }

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

    PyObject* method = (PyObject*)v8_compat::external_value(info.Data());
    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    bool direct_callback = false;
    if (!is_direct_callback(method, &direct_callback)) {
        js_throw_py();
        return;
    }
    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }

    PyObject* value = py_from_js(info[0], context);
    JS_PROPAGATE_PY(value);

    if (direct_callback) {
        PyObject* result = self == Py_None ?
            PyObject_CallFunctionObjArgs(method, value, NULL) :
            PyObject_CallFunctionObjArgs(method, self, value, NULL);
        Py_DECREF(value);
        JS_PROPAGATE_PY(result);
        Py_XDECREF(result);
        return;
    }

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

    if (!v8_receiver->SetNativeDataProperty(
            context,
            V8String(isolate, js_name),
            py_class_property_getter,
            py_class_property_setter,
            info.Data()).To(&did_create)) {
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

    PyObject* method = (PyObject*)v8_compat::external_value(info.Data());

    bool direct_callback = false;
    if (!is_direct_callback(method, &direct_callback)) {
        js_throw_py();
        return;
    }
    if (self == Py_None && PyMethod_Check_abi3(method)) {
        self = PyMethod_GET_SELF_abi3(method);
    }

    JS_PROPAGATE_PY(method);
    assert(PyFunction_Check_abi3(method));

    PyObject* value = py_from_js(info[0], context);
    JS_PROPAGATE_PY(value);

    if (direct_callback) {
        PyObject* result = self == Py_None ?
            PyObject_CallFunctionObjArgs(method, value, NULL) :
            PyObject_CallFunctionObjArgs(method, self, value, NULL);
        Py_DECREF(value);
        JS_PROPAGATE_PY(result);
        Py_XDECREF(result);
        return;
    }

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
    if (self == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Python object callback receiver is unavailable");
        return NULL;
    }
    PyObject* method = PyObject_GetAttrString(self, method_name);
    if (method == NULL && PyErr_ExceptionMatches(PyExc_AttributeError))
        PyErr_Clear();
    return method;
}

Intercepted
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

Intercepted
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
    Py_DECREF(result);
    return Intercepted::kYes;
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

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        Intercepted result;
        {
            faker::FakerHookGetter hook(info, js_name, self, ctx_c);
            result = property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_name_get__");
        }
        if (PyErr_Occurred()) {
            js_throw_py();
            return Intercepted::kYes;
        }
        return result;
    }
    else
    {
        return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_name_get__");
    }
}

Intercepted properties_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = ctx_c->py_global;

    return property_getter_handler(py_from_js(js_name, context), info, context, self, "__v8_property_name_get__");
}


Intercepted named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        Intercepted result;
        {
            faker::FakerHookSetter hook(info, js_name, js_value, self, ctx_c);
            result = property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_name_set__");
        }
        if (PyErr_Occurred()) {
            js_throw_py();
            return Intercepted::kYes;
        }
        return result;
    }
    else {
        return property_setter_handler(py_from_js(js_name, context), js_value, info, context, self, "__v8_name_set__");
    }
}

Intercepted named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_deleter_handler(py_from_js(js_name, context), info, self, "__v8_name_del__");
}

Intercepted named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_query_handler(py_from_js(js_name, context), info, self, "__v8_name_query__");
}

void named_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    named_enumerator_handler(info, context, self, "__v8_name_enum__");
}

Intercepted indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        Intercepted result;
        {
            faker::FakerHookGetter hook(info, index, self, ctx_c);
            result = property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_index_get__");
        }
        if (PyErr_Occurred()) {
            js_throw_py();
            return Intercepted::kYes;
        }
        return result;
    }
    else {
        return property_getter_handler(PyLong_FromSize_t(index), info, context, self, "__v8_index_get__");
    }
}

Intercepted indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;

    context_c* ctx_c = v8_compat::context_embedder_pointer<context_c>(
        context, CONTEXT_OBJECT_SLOT);
    if (ctx_c == nullptr)
        return Intercepted::kNo;
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        Intercepted result;
        {
            faker::FakerHookSetter hook(info, index, js_value, self, ctx_c);
            result = property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_index_set__");
        }
        if (PyErr_Occurred()) {
            js_throw_py();
            return Intercepted::kYes;
        }
        return result;
    }
    else {
        return property_setter_handler(PyLong_FromSize_t(index), js_value, info, context, self, "__v8_index_set__");
    }
}

Intercepted indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_deleter_handler(PyLong_FromSize_t(index), info, self, "__v8_index_del__");
}

Intercepted indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    return property_query_handler(PyLong_FromSize_t(index), info, self, "__v8_index_query__");
}

void indexed_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    indexed_enumerator_handler(info, context, self, "__v8_index_enum__");
}
