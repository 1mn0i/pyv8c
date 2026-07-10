#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "jsobject.h"
#include "jsvalue.h"
#include "context.h"

PyObject *
py_from_js(Local<Value> value, Local<Context> context)
{
    IN_V8;

    if (value->IsSymbol())
    {
        value = value.As<Symbol>()->Description(context->GetIsolate());
    }
    if (value->IsNull())
    {
        Py_INCREF(js_null_object);
        return js_null_object;
    }
    if (value->IsUndefined())
    {
        Py_INCREF(js_undefined_object);
        return js_undefined_object;
    }
    if (value->IsBoolean())
    {
        if (value.As<Boolean>()->Value())
            Py_RETURN_TRUE;
        Py_RETURN_FALSE;
    }

    if (value->IsString())
    {
        String::Utf8Value utf8(isolate, value.As<String>());
        return PyUnicode_DecodeUTF8(*utf8, utf8.length(), "replace");
    }

    if (value->IsArray())
    {
        Local<Array> array = value.As<Array>();
        PyObject *list = PyList_New(array->Length());
        if (!list)
            return NULL;

        for (uint32_t i = 0; i < array->Length(); i++)
        {
            Local<Value> js_value;
            if (!array->Get(context, i).ToLocal(&js_value))
            {
                Py_DECREF(list);
                return NULL;
            }

            if (js_value->StrictEquals(array))
            {
                Py_INCREF(list);
                PyList_SetItem(list, i, list);
                continue;
            }

            PyObject *obj = py_from_js(js_value, context);
            if (obj == NULL)
            {
                Py_DECREF(list);
                return NULL;
            }
            PyList_SetItem(list, i, obj);
        }
        return list;
    }

    if (value->IsObject())
    {
        Local<Object> obj_value = value.As<Object>();
        if (context.IsEmpty())
        {
            context = obj_value->GetCreationContextChecked();
        }
        bool has_to_string_tag;
        Maybe<bool> maybe_has_to_string_tag = obj_value->HasOwnProperty(context, Symbol::GetToStringTag(context->GetIsolate()));
        if (!maybe_has_to_string_tag.To(&has_to_string_tag))
        {
            PyErr_SetString(PyExc_RuntimeError, "failed to inspect JavaScript object");
            return NULL;
        }
        if (obj_value->GetPrototype()->StrictEquals(context->GetEmbedderData(OBJECT_PROTOTYPE_SLOT)) && !has_to_string_tag)
        {
            PyObject *dict = PyDict_New();
            if (!dict)
                return NULL;

            Local<Array> js_keys;
            if (!obj_value->GetPropertyNames(context).ToLocal(&js_keys))
            {
                Py_XDECREF(dict);
                PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript object properties");
                return NULL;
            }
            for (uint32_t i = 0; i < js_keys->Length(); i++)
            {
                Local<Value> js_key;
                if (!js_keys->Get(context, i).ToLocal(&js_key))
                {
                    Py_XDECREF(dict);
                    PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript object property key");
                    return NULL;
                }
                PyObject *key = py_from_js(js_key, context);
                if (key == NULL)
                {
                    Py_XDECREF(dict);
                    return NULL;
                }
                Local<Value> js_value;
                if (!obj_value->Get(context, js_key).ToLocal(&js_value))
                {
                    Py_XDECREF(key);
                    Py_XDECREF(dict);
                    PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript object property value");
                    return NULL;
                }
                if (js_value->StrictEquals(obj_value))
                {
                    if (PyDict_SetItem(dict, key, dict) < 0)
                    {
                        Py_XDECREF(key);
                        Py_XDECREF(dict);
                        return NULL;
                    }
                    Py_XDECREF(key);
                    continue;
                }
                PyObject *dict_value = py_from_js(js_value, context);
                if (dict_value == NULL)
                {
                    Py_XDECREF(dict);
                    Py_XDECREF(key);
                    return NULL;
                }
                if (PyDict_SetItem(dict, key, dict_value) < 0)
                {
                    Py_XDECREF(key);
                    Py_XDECREF(dict_value);
                    Py_XDECREF(dict);
                    return NULL;
                }
                Py_XDECREF(key);
                Py_XDECREF(dict_value);
            }
            return dict;
        }

        if (obj_value->InternalFieldCount() == OBJECT_INTERNAL_FIELDS)
        {
            // Local<Value> magic = obj_value->GetInternalField(0);
            Local<Data> magic = obj_value->GetInternalField(0);
            if (magic == IZ_DAT_OBJECT)
            {
                PyObject *object = (PyObject *)obj_value->GetInternalField(1).As<External>()->Value();
                Py_INCREF(object);
                return object;
            }
        }
        return (PyObject *)js_object_new(obj_value, context);
    }

    if (value->IsUint32() || value->IsInt32())
    {
        return PyLong_FromLongLong((PY_LONG_LONG)value.As<Integer>()->Value());
    }
    if (value->IsNumber())
    {
        return PyFloat_FromDouble(value.As<Number>()->Value());
    }

    if (value->IsBigInt()) {
        return (PyObject*)js_value_new(value, context);
    }
    // i'm not quite ready to turn this into an assert quite yet
    // i'll do that when I've special-cased every primitive type
    printf("cannot convert\n");
    Py_RETURN_NONE;
}

Local<Value>
js_from_py(PyObject *value, Local<Context> context)
{
    ESCAPING_IN_V8;

    if (value == Py_False)
    {
        return hs.Escape(False(isolate));
    }
    if (value == Py_True)
    {
        return hs.Escape(True(isolate));
    }
    if (value == js_undefined_object)
    {
        return hs.Escape(Undefined(isolate));
    }
    if (value == Py_None)
    {
        return hs.Escape(Undefined(isolate));
    }
    if (value == js_null_object)
    {
        return hs.Escape(Null(isolate));
    }
    if (PyUnicode_Check(value))
    {
        Py_ssize_t len;
        const char *str = PyUnicode_AsUTF8AndSize(value, &len);
        if (str == NULL)
        {
            return Local<Value>();
        }
        Local<String> js_value;
        if (!String::NewFromUtf8(isolate, str, NewStringType::kNormal, len).ToLocal(&js_value))
        {
            PyErr_SetString(PyExc_RuntimeError, "failed to create JavaScript string");
            return Local<Value>();
        }
        return hs.Escape(js_value);
    }
    if (PyBytes_Check(value))
    {
        char *str;
        Py_ssize_t len;
        if (PyBytes_AsStringAndSize(value, &str, &len) < 0)
        {
            return hs.Escape(Undefined(isolate));
        }
        Local<ArrayBuffer> js_value = ArrayBuffer::New(isolate, static_cast<size_t>(len));
        if (len > 0)
        {
            memcpy(js_value->Data(), str, static_cast<size_t>(len));
        }
        return hs.Escape(js_value);
    }

    if (PyNumber_Check(value))
    {
        Local<Number> js_value;
        if (PyFloat_Check(value))
        {
            js_value = Number::New(isolate, PyFloat_AsDouble(value));
        }
        else if (PyLong_Check(value))
        {
            long long_value = PyLong_AsLong(value);
            if (PyErr_Occurred())
            {
                PyErr_Clear();
                js_value = Number::New(isolate, static_cast<double>(PyLong_AsLongLong(value)));
            }
            else
            {
                js_value = Integer::New(isolate, long_value);
            }
        }
        else
        {
            // TODO make this work right
            printf("what the hell kind of number is this?!");
            return hs.Escape(Undefined(isolate));
        }
        return hs.Escape(js_value);
    }

    if (PyDict_Check(value))
    {
        // a context scope is (I think) needed for Object::New to work
        Context::Scope cs(context);
        Local<Object> js_dict = Object::New(isolate);

        PyObject *dict = value;
        PyObject *key, *value;
        Py_ssize_t pos = 0;
        while (PyDict_Next(dict, &pos, &key, &value))
        {
            Local<Value> js_key = js_from_py(key, context);
            if (PyErr_Occurred())
            {
                return Local<Value>();
            }
            Local<Value> js_value;
            if (dict == value)
                js_value = js_dict;
            else
            {
                js_value = js_from_py(value, context);
                if (PyErr_Occurred())
                {
                    return Local<Value>();
                }
            }
            bool set_worked;
            if (!js_dict->Set(context, js_key, js_value).To(&set_worked))
            {
                PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript object property");
                return Local<Value>();
            }
        }
        return hs.Escape(js_dict);
    }

    if (PyList_Check(value) || PyTuple_Check(value))
    {
        int length = PySequence_Length(value);
        Local<Array> array = Array::New(isolate, length);
        for (int i = 0; i < length; i++)
        {
            PyObject *item = PySequence_GetItem(value, i);
            if (item == NULL)
            {
                return Local<Value>();
            }
            bool set_worked;
            if (item == value)
            {
                if (!array->Set(context, i, array).To(&set_worked))
                {
                    Py_DECREF(item);
                    PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript array item");
                    return Local<Value>();
                }
            }
            else
            {
                Local<Value> js_item = js_from_py(item, context);
                if (PyErr_Occurred())
                {
                    Py_DECREF(item);
                    return Local<Value>();
                }
                if (!array->Set(context, i, js_item).To(&set_worked))
                {
                    Py_DECREF(item);
                    PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript array item");
                    return Local<Value>();
                }
            }
            assert(set_worked);
            Py_DECREF(item);
        }
        return hs.Escape(array);
    }
    if (PyFunction_Check_abi3(value) || PyMethod_Check_abi3(value))
    {
        py_function *templ = (py_function *)py_function_to_template(value);
        if (templ == NULL)
        {
            return Local<Value>();
        }
        Local<Function> function = py_template_to_function(templ, context);
        if (function.IsEmpty())
        {
            return Local<Value>();
        }
        return hs.Escape(function);
    }

    if (PyType_Check(value))
    {
        context_c *ctx_c = (context_c *)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
        py_class *templ = (py_class *)py_class_to_template(value, ctx_c);
        if (templ->to_obj)
        {
            const char *world = PyUnicode_AsUTF8_abi3(templ->cls_name);
            Local<String> v8_name = V8String(isolate, world);
            bool hook = ctx_c->hook;
            ctx_c->hook = false;
            if (templ->to_obj == 1)
            {
                v8::Local<v8::ObjectTemplate> object_template = templ->templ.Get(isolate).As<v8::ObjectTemplate>();
                Local<Object> new_instance;
                if (!object_template->NewInstance(context).ToLocal(&new_instance))
                {
                    ctx_c->hook = hook;
                    PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript object template");
                    return Local<Value>();
                }
                bool set_worked;
                if (!context->Global()->Set(context, v8_name, new_instance).To(&set_worked))
                {
                    ctx_c->hook = hook;
                    PyErr_SetString(PyExc_RuntimeError, "failed to expose JavaScript object template");
                    return Local<Value>();
                }
                // templ->to_obj += 1;
            }
            Local<Value> obj;
            if (!context->Global()->Get(context, v8_name).ToLocal(&obj))
            {
                ctx_c->hook = hook;
                PyErr_SetString(PyExc_RuntimeError, "failed to retrieve JavaScript object template");
                return Local<Value>();
            }
            ctx_c->hook = hook;
            return hs.Escape(obj);
        }
        Local<Function> constructor = py_class_get_constructor(context, templ, (py_class *)templ->parent);
        if (constructor.IsEmpty())
        {
            return Local<Value>();
        }
        return hs.Escape(constructor);
    }
    if (PyObject_TypeCheck(value, js_object_type))
    {
        js_object *py_value = (js_object *)value;
        return hs.Escape(py_value->object.Get(isolate));
    }

    if (PyObject_TypeCheck(value, js_value_type))
    {
        js_value* py_value = (js_value*)value;
        return hs.Escape(py_value->value.Get(isolate));
    }

    // python global_this -> js globalThis
    context_c *ctx_c = (context_c *)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (ctx_c->py_global != NULL && Py_TYPE(value) == Py_TYPE(ctx_c->py_global))
    {
        PyObject *ctx = PyObject_GetAttrString(value, "_ctx");
        if (ctx == NULL)
        {
            if (PyErr_Occurred())
                PyErr_Clear();
            return context->Global();
        }

        auto js_content = ((context_c *)ctx)->js_context.Get(isolate);
        Py_DECREF(ctx);
        return js_content->Global();
    }
    // it's an arbitrary object
    PyObject *type;
    type = (PyObject*)Py_TYPE(value);
    py_class *templ = (py_class *)py_class_to_template(type, ctx_c);
    if (templ == NULL)
    {
        return Local<Value>();
    }
    Local<Object> js_object = py_class_create_js_object(templ, value, context);
    if (js_object.IsEmpty())
    {
        return Local<Value>();
    }
    return hs.Escape(js_object);
}

PyObject *
pys_from_jss(const FunctionCallbackInfo<Value> &js_args, Local<Context> context)
{
    PyObject *py_args = PyTuple_New(js_args.Length());
    PyErr_PROPAGATE(py_args);
    for (int i = 0; i < js_args.Length(); i++)
    {
        PyObject *arg = py_from_js(js_args[i], context);
        if (arg == NULL)
        {
            Py_DECREF(py_args);
            return NULL;
        }
        PyTuple_SetItem(py_args, i, arg);
    }
    return py_args;
}

// js_args is an out parameter, expected to contain enough space
void jss_from_pys(PyObject *py_args, Local<Value> *js_args, Local<Context> context)
{
    for (int i = 0; i < PyTuple_Size(py_args); i++)
    {
        js_args[i] = js_from_py(PyTuple_GetItem(py_args, i), context);
    }
}

Local<Object>
pyexception_to_jsexception(PyObject *value, Local<Context> context)
{
    Local<Object> exception;
    if (!PyExceptionInstance_Check(value))
    {
        return exception;
    }
    if (!PyObject_HasAttrString(value, "__v8_error__"))
    {
        return exception;
    }
    PyObject *exc_value_str = PyObject_Str(value);
    if (exc_value_str == NULL)
    {
        return exception;
    }
    const char *exc_message = PyUnicode_AsUTF8_abi3(exc_value_str);
    if (exc_message == NULL)
    {
        Py_XDECREF(exc_value_str);
        return exception;
    }
    PyObject *exc_type = PyObject_Type(value);
    if (exc_type == NULL)
    {
        Py_XDECREF(exc_value_str);
        return exception;
    }
    PyObject *exc_cls_name = PyObject_GetAttrString(exc_type, "__name__");
    if (exc_cls_name == NULL)
    {
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value_str);
        return exception;
    }
    const char *exc_type_name = PyUnicode_AsUTF8_abi3(exc_cls_name);
    if (exc_type_name == NULL)
    {
        Py_XDECREF(exc_cls_name);
        Py_XDECREF(exc_type);
        Py_XDECREF(exc_value_str);
        return exception;
    }
    if (PyObject_TypeCheck(value, js_exception_type))
    {
        exception = ((js_exception *)value)->exception.Get(isolate).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8TypeError") == 0)
    {
        exception = Exception::TypeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8Error") == 0)
    {
        exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8RangeError") == 0)
    {
        exception = Exception::RangeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8ReferenceError") == 0)
    {
        exception = Exception::ReferenceError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8SyntaxError") == 0)
    {
        exception = Exception::SyntaxError(V8String(isolate, exc_message)).As<Object>();
    }
    else
    {
        exception = js_from_py(value, context).As<Object>();
        Py_INCREF(exc_type);
        exception->SetInternalField(2, External::New(isolate, exc_type));
        exception->Set(context, V8String(isolate, "message"), V8String(isolate, exc_message));
    }
    Py_XDECREF(exc_cls_name);
    Py_XDECREF(exc_type);
    Py_XDECREF(exc_value_str);
    return exception;
}
