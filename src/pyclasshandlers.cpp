#include <v8.h>
#include <Python.h>
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



template <class T> 
inline extern PyObject* get_self(const PropertyCallbackInfo<T>& info) {
    Local<Context> context = isolate->GetCurrentContext();
    Local<Object> js_self = info.Holder();
    if (js_self == context->Global()) {
        js_self = js_self->GetPrototype().As<Object>();
    }

    return (PyObject*)js_self->GetInternalField(PYTHON_OBJECT_FIELD).template As<External>()->Value();
}

template <class T> 
inline extern PyObject* get_self(const FunctionCallbackInfo<T>& info) {
    Local<Context> context = isolate->GetCurrentContext();
    Local<Object> js_self = info.Holder();
    if (js_self == context->Global()) {
        js_self = js_self->GetPrototype().As<Object>();
    }
    if (js_self->InternalFieldCount() > 0) {
        return (PyObject*)js_self->GetInternalField(PYTHON_OBJECT_FIELD).template As<External>()->Value();
    }
    return Py_None;
}

template <class T>
inline extern PyObject* get_self_cross_origin(const PropertyCallbackInfo<T>& info) {
    Local<Context> context = isolate->GetCurrentContext();
    Local<Object> js_self = info.Holder();
    if (js_self->Equals(context, context->Global()).IsJust()) {
        js_self = js_self->GetPrototype().As<Object>();
    }

    return (PyObject*)js_self->GetInternalField(PYTHON_OBJECT_FIELD).template As<External>()->Value();
}

template <class T>
inline extern PyObject* get_self_cross_origin(const FunctionCallbackInfo<T>& info) {
    Local<Context> context = isolate->GetCurrentContext();
    Local<Object> js_self = info.Holder();
    if (js_self->Equals(context, context->Global()).IsJust()) {
        js_self = js_self->GetPrototype().As<Object>();
    }
    if (js_self->InternalFieldCount() > 0) {
        return (PyObject*)js_self->GetInternalField(PYTHON_OBJECT_FIELD).template As<External>()->Value();
    }
    return Py_None;
}

#define Info(T) const PropertyCallbackInfo<T> &info


#define PY_CLASS_CONSTRUCT_CALLBACK_CODE                                                                                                                            \
    PyObject* class_dict = PyObject_GetAttrString(self->cls, "__dict__");                                                                                           \
    int flag = 0;                                                                                                                                                   \
    if (PyMapping_HasKeyString(class_dict, "__v8_constructor__")) {                                                                                                 \
        PyObject* constructor_behavior = PyMapping_GetItemString(class_dict, "__v8_constructor__");                                                                 \
        if (PyLong_Check(constructor_behavior)) {                                                                                                                   \
            flag = PyLong_AsLong(constructor_behavior);                                                                                                             \
        }                                                                                                                                                           \
        Py_XDECREF(constructor_behavior);                                                                                                                           \
    }                                                                                                                                                               \
    Py_XDECREF(class_dict);                                                                                                                                         \
    bool can_new = (flag & FlagConstructor::kNew) != 0;                                                                                                             \
    bool can_call = (flag & FlagConstructor::kCall) != 0;                                                                                                           \
                                                                                                                                                                    \
                                                                                                                                                                    \
    if (!can_call && !can_new) {                                                                                                                                    \
        isolate->ThrowException(Exception::TypeError(JSTR("Illegal constructor")));                                                                                 \
        return;                                                                                                                                                     \
    }                                                                                                                                                               \
    if (!can_call && !info.IsConstructCall()) {                                                                                                                     \
        std::ostringstream oss;                                                                                                                                     \
        const char* cls_name = PyUnicode_AsUTF8_abi3(self->cls_name);                                                                                               \
        oss << "Failed to construct '" << std::string(cls_name) << "': Please use the 'new' operator, this DOM object constructor cannot be called as a function."; \
        isolate->ThrowException(Exception::TypeError(JSTR(oss.str().c_str())));                                                                                     \
        return;                                                                                                                                                     \
    }                                                                                                                                                               \
    if (PyObject_HasAttrString(self->cls, "__v8_unconstructable__")) {                                                                                              \
        PyObject* format_args = Py_BuildValue("O", self->cls_name);                                                                                                 \
        JS_PROPAGATE_PY(format_args);                                                                                                                               \
        PyObject* format_string = PyUnicode_FromString("%s is not a constructor");                                                                                  \
        if (format_string == NULL) {                                                                                                                                \
            Py_XDECREF(format_args);                                                                                                                                \
            js_throw_py();                                                                                                                                          \
            return;                                                                                                                                                 \
        }                                                                                                                                                           \
        PyObject* message = PyUnicode_Format(format_string, format_args);                                                                                           \
        JS_PROPAGATE_PY(message);                                                                                                                                   \
        isolate->ThrowException(Exception::TypeError(js_from_py(message, context).As<String>()));                                                                   \
        Py_XDECREF(format_string);                                                                                                                                  \
        Py_XDECREF(message);                                                                                                                                        \
    }                                                                                                                                                               \
                                                                                                                                                                    \
    Local<Object> js_new_object = info.Holder();                                                                                                                    \
    PyObject* kwargs = PyDict_New();                                                                                                                                \
    if (PyDict_SetItemString(kwargs, "win", ctx_c->py_global) != 0) {                                                                                               \
        Py_XDECREF(kwargs);                                                                                                                                         \
        js_throw_py();                                                                                                                                              \
    }                                                                                                                                                               \
    PyObject* args = pys_from_jss(info, context);                                                                                                                   \
    PyObject* new_object = PyObject_Call(self->cls, args, kwargs);                                                                                                  \
    Py_XDECREF(args);                                                                                                                                               \
    Py_XDECREF(kwargs);                                                                                                                                             \
    JS_PROPAGATE_PY(new_object);                                                                                                                                    \
    if (!py_class_init_js_object(js_new_object, new_object, context)) {                                                                                            \
        Py_XDECREF(new_object);                                                                                                                                    \
        js_throw_py();                                                                                                                                             \
        return;                                                                                                                                                    \
    }                                                                                                                                                               \
    Py_DECREF(new_object);


void py_class_construct_callback(const FunctionCallbackInfo<Value>& info) {
    SETUP;

    py_class *self = (py_class *) info.Data().As<External>()->Value();
    context_c* ctx_c = (context_c*)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (ctx_c->hook) {
        faker::FakerHookConstruct construct_hook(info, PyUnicode_AsUTF8_abi3(self->cls_name),  self->cls, ctx_c);

        PY_CLASS_CONSTRUCT_CALLBACK_CODE;
    }
    else {

        PY_CLASS_CONSTRUCT_CALLBACK_CODE

    }
}

#define PY_CLASS_METHOD_CALLBACK_CODE                                    \
    PyObject* args = pys_from_jss(info, context);                        \
    JS_PROPAGATE_PY(args);                                               \
    PyObject* method_py = PyObject_GetAttrString(self, method_name_str); \
    JS_PROPAGATE_PY(method_py);                                          \
    PyObject* retval = PyObject_Call(method_py, args, NULL);             \
    Py_XDECREF(method_py);                                               \
    Py_XDECREF(args);                                                    \
    JS_PROPAGATE_PY(retval);                                             \
    info.GetReturnValue().Set(js_from_py(retval, context));              \
    Py_XDECREF(retval);


#define PY_CLASS_CROSS_ORIGIN_METHOD_CALLBACK_CODE                                                                          \
    PyObject* args = pys_from_jss(info, context);                                                                           \
    JS_PROPAGATE_PY(args);                                                                                                  \
    PyObject* method_py = PyObject_GetAttrString(self, method_name_str);                                                    \
    JS_PROPAGATE_PY(method_py);                                                                                             \
    PyObject* kwargs = PyDict_New();                                                                                        \
    Local<Context> incumbent_ctx = isolate->GetIncumbentContext();                                                          \
    context_c* incumbent_ctx_c = (context_c*)incumbent_ctx->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();   \
    if (PyDict_SetItemString(kwargs, "win", incumbent_ctx_c->py_global) != 0) {                                             \
        Py_XDECREF(kwargs);                                                                                                 \
        js_throw_py();                                                                                                      \
    }                                                                                                                       \
    PyObject* retval = PyObject_Call(method_py, args, kwargs);                                                              \
    Py_XDECREF(kwargs);                                                                                                     \
    Py_XDECREF(method_py);                                                                                                  \
    Py_XDECREF(args);                                                                                                       \
    JS_PROPAGATE_PY(retval);                                                                                                \
    info.GetReturnValue().Set(js_from_py(retval, context));                                                                 \
    Py_XDECREF(retval);  


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
        PY_CLASS_CROSS_ORIGIN_METHOD_CALLBACK_CODE
    }
    else {
        PY_CLASS_CROSS_ORIGIN_METHOD_CALLBACK_CODE
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
        PY_CLASS_METHOD_CALLBACK_CODE
    }
    else {
        PY_CLASS_METHOD_CALLBACK_CODE
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


#define NAMED_GETTER_CODE(py_method_name)                                             \
    PyObject* name = py_from_js(js_name, context);                                    \
    if (name == NULL) {                                                               \
        js_throw_py();                                                                \
        return Intercepted::kNo;                                                      \
    };                                                                                \
    if (PyObject_HasAttrString(self, py_method_name)) {                               \
        PyObject* value = PyObject_CallMethod(self, py_method_name, "O", name);       \
        Py_XDECREF(name);                                                             \
        if (value == NULL) {                                                          \
            js_throw_py();                                                            \
            return Intercepted::kNo;                                                  \
        }                                                                             \
        if (value == Py_None) {                                                       \
            return Intercepted::kNo;                                                  \
        }                                                                             \
        info.GetReturnValue().Set(js_from_py(value, context));                        \
        Py_XDECREF(value);                                                            \
        return Intercepted::kYes;                                                     \
    }                                                                                 \
    Py_XDECREF(name);                                                                 \
    return Intercepted::kNo;


Intercepted named_getter(Local<Name> js_name, Info(Value)) { 
    SETUP

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookGetter hook(info, js_name, self, ctx_c);
        NAMED_GETTER_CODE("__v8_name_get__")
    }
    else
    {
        NAMED_GETTER_CODE("__v8_name_get__")
    }
}

Intercepted properties_named_getter(Local<Name> js_name, Info(Value)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = ctx_c->py_global;

    NAMED_GETTER_CODE("__v8_property_name_get__")
}

Intercepted cross_origin_named_getter(Local<Name> js_name, Info(Value)){
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    NAMED_GETTER_CODE("__v8_cross_origin_name_get__")

}



#define NAMED_SETTER_CODE(py_method_name)                                                   \
    PyObject* name = py_from_js(js_name, context);                                          \
    if (name == NULL) {                                                                     \
        js_throw_py();                                                                      \
        return Intercepted::kNo;                                                            \
    };                                                                                      \
                                                                                            \
    PyObject* value = py_from_js(js_value, context);                                        \
    if (value == NULL) {                                                                    \
        Py_XDECREF(name);                                                                   \
        js_throw_py();                                                                      \
        return Intercepted::kNo;                                                            \
    }                                                                                       \
    if (PyObject_HasAttrString(self, py_method_name)) {                                     \
        PyObject* ret = PyObject_CallMethod(self, py_method_name, "OO", name, value);       \
        Py_XDECREF(name);                                                                   \
        Py_XDECREF(value);                                                                  \
        if (ret == NULL) {                                                                  \
            js_throw_py();                                                                  \
            return Intercepted::kNo;                                                        \
        }                                                                                   \
        if (ret == Py_None) {                                                               \
            return Intercepted::kNo;                                                        \
        }                                                                                   \
        info.GetReturnValue().Set(js_from_py(ret, context));                                \
        Py_XDECREF(ret);                                                                    \
        return Intercepted::kYes;                                                           \
    }                                                                                       \
    Py_XDECREF(name);                                                                       \
    Py_XDECREF(value);                                                                      \
    return Intercepted::kNo;

Intercepted named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);
    
    if (ctx_c->hook) {
        faker::FakerHookSetter hook(info, js_name, js_value, self, ctx_c);
        NAMED_SETTER_CODE("__v8_name_set__")
    }
    else {
        NAMED_SETTER_CODE("__v8_name_set__")
    }
}

Intercepted cross_origin_named_setter(Local<Name> js_name, Local<Value> js_value, Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    NAMED_SETTER_CODE("__v8_cross_origin_name_set__")
}

#define NAMED_DELETER_CODE(py_method_name)                                                \
    PyObject *name = py_from_js(js_name, context);                                        \
    if (name == NULL)                                                                     \
    {                                                                                     \
        js_throw_py();                                                                    \
        return Intercepted::kNo;                                                          \
    };                                                                                    \
                                                                                          \
    if (PyObject_HasAttrString(self, py_method_name))                                     \
    {                                                                                     \
        PyObject *value = PyObject_CallMethod(self, py_method_name, "O", name);           \
        Py_XDECREF(name);                                                                 \
        if (value == NULL)                                                                \
        {                                                                                 \
            js_throw_py();                                                                \
            return Intercepted::kNo;                                                      \
        }                                                                                 \
        if (value == Py_None)                                                             \
        {                                                                                 \
            return Intercepted::kNo;                                                      \
        }                                                                                 \
        if (!PyBool_Check(value))                                                         \
        {                                                                                 \
            Py_XDECREF(value);                                                            \
            return Intercepted::kNo;                                                      \
        }                                                                                 \
        info.GetReturnValue().Set(Boolean::New(isolate, value == Py_True));               \
        Py_XDECREF(value);                                                                \
        return Intercepted::kYes;                                                         \
    }                                                                                     \
    Py_XDECREF(name);                                                                     \
    return Intercepted::kNo;

Intercepted named_deleter(Local<Name> js_name, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    NAMED_DELETER_CODE("__v8_name_del__")
}

Intercepted cross_origin_named_deleter(Local<Name> js_name, Info(Boolean)){
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    NAMED_DELETER_CODE("__v8_cross_origin_name_del__")
}

#define NAMED_QUERY_CODE(py_method_name)                                             \
    PyObject *name = py_from_js(js_name, context);                                   \
    if (name == NULL)                                                                \
    {                                                                                \
        js_throw_py();                                                               \
        return Intercepted::kNo;                                                     \
    };                                                                               \
                                                                                     \
    if (PyObject_HasAttrString(self, py_method_name))                                \
    {                                                                                \
        PyObject *value = PyObject_CallMethod(self, py_method_name, "O", name);      \
        Py_XDECREF(name);                                                            \
        if (value == NULL)                                                           \
        {                                                                            \
            js_throw_py();                                                           \
            return Intercepted::kNo;                                                 \
        }                                                                            \
        if (PyLong_Check(value))                                                     \
        {                                                                            \
            info.GetReturnValue().Set(Integer::New(isolate, PyLong_AsLong(value)));  \
            Py_XDECREF(value);                                                       \
            return Intercepted::kYes;                                                \
        }                                                                            \
        Py_XDECREF(value);                                                           \
        return Intercepted::kNo;                                                     \
    }                                                                                \
    Py_XDECREF(name);                                                                \
    return Intercepted::kNo;

Intercepted named_query(Local<Name> js_name, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    NAMED_QUERY_CODE("__v8_name_query__")
}

Intercepted cross_origin_named_query(Local<Name> js_name, Info(Integer)){
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    NAMED_QUERY_CODE("__v8_cross_origin_name_query__")
}

#define NAMED_ENUM_CODE(py_method_name)                                           \
    PyObject *keys = PyObject_CallMethod(self, py_method_name, "");               \
    JS_PROPAGATE_PY(keys);                                                        \
    if (keys == Py_None)                                                          \
    {                                                                             \
        return;                                                                   \
    }                                                                             \
    if (!PySequence_Check(keys))                                                  \
    {                                                                             \
        Py_XDECREF(keys);                                                         \
        return;                                                                   \
    }                                                                             \
    Local<Array> js_keys = Array::New(isolate, PySequence_Length(keys));          \
    for (int i = 0; i < PySequence_Length(keys); i++)                             \
    {                                                                             \
        PyObject *item = PySequence_GetItem(keys, i);                             \
        if (item == NULL)                                                         \
        {                                                                         \
            Py_XDECREF(keys);                                                     \
            js_throw_py();                                                        \
            return;                                                               \
        }                                                                         \
        Local<Value> js_item = js_from_py(item, context);                         \
        Py_XDECREF(item);                                                         \
        if (PyErr_Occurred())                                                     \
        {                                                                         \
            Py_XDECREF(keys);                                                     \
            js_throw_py();                                                        \
            return;                                                               \
        }                                                                         \
        bool set_worked;                                                          \
        if (!js_keys->Set(context, i, js_item).To(&set_worked))                   \
        {                                                                         \
            Py_XDECREF(keys);                                                     \
            js_throw_py();                                                        \
            return;                                                               \
        }                                                                         \
    }                                                                             \
    Py_XDECREF(keys);                                                             \
    info.GetReturnValue().Set(js_keys);

void named_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    NAMED_ENUM_CODE("__v8_name_enum__")
}

void cross_origin_named_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    NAMED_ENUM_CODE("__v8_cross_origin_name_enum__")
}

#define INDEXED_GETTER_CODE(py_method_name)                                          \
    PyObject* idx = PyLong_FromSize_t(index);                                        \
    if (idx == NULL) {                                                               \
        js_throw_py();                                                               \
        return Intercepted::kNo;                                                     \
    };                                                                               \
                                                                                     \
    if (PyObject_HasAttrString(self, py_method_name)) {                              \
        PyObject* value = PyObject_CallMethod(self, py_method_name, "O", idx);       \
        Py_XDECREF(idx);                                                             \
        if (value == NULL) {                                                         \
            js_throw_py();                                                           \
            return Intercepted::kNo;                                                 \
        }                                                                            \
        if (value == Py_None) {                                                      \
            return Intercepted::kNo;                                                 \
        }                                                                            \
        info.GetReturnValue().Set(js_from_py(value, context));                       \
        Py_XDECREF(value);                                                           \
        return Intercepted::kYes;                                                    \
    }                                                                                \
    Py_XDECREF(idx);                                                                 \
    return Intercepted::kNo;                                                         

Intercepted indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookGetter hook(info, index, self, ctx_c);
        INDEXED_GETTER_CODE("__v8_index_get__")
    }
    else {
        INDEXED_GETTER_CODE("__v8_index_get__")
    }
}

Intercepted cross_origin_indexed_getter(uint32_t index, Info(Value)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    INDEXED_GETTER_CODE("__v8_cross_origin_index_get__")
}


#define INDEXED_SETTER_CODE(py_method_name)                                             \
    PyObject* idx = PyLong_FromSize_t(index);                                           \
    if (idx == NULL) {                                                                  \
        js_throw_py();                                                                  \
        return Intercepted::kNo;                                                        \
    };                                                                                  \
                                                                                        \
    PyObject* value = py_from_js(js_value, context);                                    \
    if (value == NULL) {                                                                \
        Py_XDECREF(idx);                                                                \
        js_throw_py();                                                                  \
        return Intercepted::kNo;                                                        \
    }                                                                                   \
    if (PyObject_HasAttrString(self, py_method_name)) {                                 \
        PyObject* ret = PyObject_CallMethod(self, py_method_name, "OO", idx, value);    \
        Py_XDECREF(idx);                                                                \
        Py_XDECREF(value);                                                              \
        if (ret == NULL) {                                                              \
            js_throw_py();                                                              \
            return Intercepted::kNo;                                                    \
        }                                                                               \
        if (ret == Py_None) {                                                           \
            return Intercepted::kNo;                                                    \
        }                                                                               \
        info.GetReturnValue().Set(js_from_py(ret, context));                            \
        Py_XDECREF(ret);                                                                \
        return Intercepted::kYes;                                                       \
    }                                                                                   \
    Py_XDECREF(idx);                                                                    \
    Py_XDECREF(value);                                                                  \
    return Intercepted::kNo;

Intercepted indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;

    context_c* ctx_c = (context_c*)info.GetIsolate()->GetCurrentContext()->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    PyObject* self = get_self(info);

    if (ctx_c->hook) {
        faker::FakerHookSetter hook(info, index, js_value, self, ctx_c);
        INDEXED_SETTER_CODE("__v8_index_set__")
    }
    else {
        INDEXED_SETTER_CODE("__v8_index_set__")
    }
}

Intercepted cross_origin_indexed_setter(uint32_t index, Local<Value> js_value, Info(void)) {
    SETUP;

    PyObject* self = get_self_cross_origin(info);

    INDEXED_SETTER_CODE("__v8_cross_origin_index_set__")
}

#define INDEXED_DELETER_CODE(py_method_name)                                       \
    PyObject *idx = PyLong_FromSize_t(index);                                      \
    if (idx == NULL)                                                               \
    {                                                                              \
        js_throw_py();                                                             \
        return Intercepted::kNo;                                                   \
    };                                                                             \
                                                                                   \
    if (PyObject_HasAttrString(self, py_method_name))                              \
    {                                                                              \
        PyObject *value = PyObject_CallMethod(self, py_method_name, "O", idx);     \
        Py_XDECREF(idx);                                                           \
        if (value == NULL)                                                         \
        {                                                                          \
            js_throw_py();                                                         \
            return Intercepted::kNo;                                               \
        }                                                                          \
        if (value == Py_None)                                                      \
        {                                                                          \
            return Intercepted::kNo;                                               \
        }                                                                          \
        if (!PyBool_Check(value))                                                  \
        {                                                                          \
            Py_XDECREF(value);                                                     \
            return Intercepted::kNo;                                               \
        }                                                                          \
        info.GetReturnValue().Set(Boolean::New(isolate, value == Py_True));        \
        Py_XDECREF(value);                                                         \
        return Intercepted::kYes;                                                  \
    }                                                                              \
    Py_XDECREF(idx);                                                               \
    return Intercepted::kNo;

Intercepted indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self(info);
    INDEXED_DELETER_CODE("__v8_index_del__")
}

Intercepted cross_origin_indexed_deleter(uint32_t index, Info(Boolean)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    INDEXED_DELETER_CODE("__v8_cross_origin_index_del__")
}

#define INDEXED_QUERY_CODE(py_method_name)                                           \
    PyObject *idx = PyLong_FromSize_t(index);                                        \
    if (idx == NULL)                                                                 \
    {                                                                                \
        js_throw_py();                                                               \
        return Intercepted::kNo;                                                     \
    };                                                                               \
                                                                                     \
    if (PyObject_HasAttrString(self, py_method_name))                                \
    {                                                                                \
        PyObject *value = PyObject_CallMethod(self, py_method_name, "O", idx);       \
        Py_XDECREF(idx);                                                             \
        if (value == NULL)                                                           \
        {                                                                            \
            js_throw_py();                                                           \
            return Intercepted::kNo;                                                 \
        }                                                                            \
        if (PyLong_Check(value))                                                     \
        {                                                                            \
            info.GetReturnValue().Set(Integer::New(isolate, PyLong_AsLong(value)));  \
            Py_XDECREF(value);                                                       \
            return Intercepted::kYes;                                                \
        }                                                                            \
        Py_XDECREF(value);                                                           \
        return Intercepted::kNo;                                                     \
    }                                                                                \
    Py_XDECREF(idx);                                                                 \
    return Intercepted::kNo;

Intercepted indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    PyObject* self = get_self(info);
    INDEXED_QUERY_CODE("__v8_index_query__")
}

Intercepted cross_origin_indexed_query(uint32_t index, Info(Integer)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    INDEXED_QUERY_CODE("__v8_cross_origin_index_query__")
}

#define INDEXED_ENUM_CODE(py_method_name)                                         \
    PyObject *len = PyObject_CallMethod(self, py_method_name, "");                \
    JS_PROPAGATE_PY(len);                                                         \
                                                                                  \
    if (len == Py_None)                                                           \
    {                                                                             \
        return;                                                                   \
    }                                                                             \
    if (!PyLong_Check(len))                                                       \
    {                                                                             \
        Py_XDECREF(len);                                                          \
        return;                                                                   \
    }                                                                             \
                                                                                  \
    int length = PyLong_AsLong(len);                                              \
    Local<Array> keys = Array::New(isolate, length);                              \
    for (int i = 0; i < length; i++)                                              \
    {                                                                             \
        keys->Set(context, i, Integer::New(isolate, i));                          \
    }                                                                             \
    info.GetReturnValue().Set(keys);

void indexed_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self(info);
    INDEXED_ENUM_CODE("__v8_index_enum__")
}

void cross_origin_indexed_enumerator(Info(Array)) {
    SETUP;
    PyObject* self = get_self_cross_origin(info);
    INDEXED_ENUM_CODE("__v8_cross_origin_index_enum__")    
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
