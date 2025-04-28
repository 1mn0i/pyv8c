#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "jsobject.h"
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
        return value.As<Boolean>()->Value() ? Py_True : Py_False;
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
        if (obj_value->GetPrototype()->StrictEquals(context->GetEmbedderData(OBJECT_PROTOTYPE_SLOT)) && !obj_value->HasOwnProperty(context, Symbol::GetToStringTag(context->GetIsolate())).ToChecked())
        {
            PyObject *dict = PyDict_New();
            if (!dict)
                return NULL;

            Local<Array> js_keys = obj_value->GetPropertyNames(context).ToLocalChecked();
            for (uint32_t i = 0; i < js_keys->Length(); i++)
            {
                Local<Value> js_key = js_keys->Get(context, i).ToLocalChecked();
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
                    continue;
                }
                if (js_value->StrictEquals(obj_value))
                {
                    Py_INCREF(dict);
                    PyDict_SetItem(dict, key, dict);
                    Py_XDECREF(key);
                    Py_XDECREF(dict);
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
        Local<String> js_value = String::NewFromUtf8(isolate, str, NewStringType::kNormal, len).ToLocalChecked();
        return hs.Escape(js_value);
    }
    if (PyString_Check(value))
    {
        char *str;
        Py_ssize_t len;
        PyBytes_AsStringAndSize(value, &str, &len);
        Local<ArrayBuffer> js_value = ArrayBuffer::New(isolate, len);
        // memcpy(str, js_value->GetContents().Data(), len);
        memcpy(str, js_value->Data(), len);
        return hs.Escape(js_value);
    }

    if (PyNumber_Check(value) && !PyInstance_Check(value))
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
            if (dict == value)
                js_dict->Set(context, js_from_py(key, context), js_dict).FromJust();
            else
                js_dict->Set(context, js_from_py(key, context), js_from_py(value, context)).FromJust();
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
            bool set_worked;
            if (item == value)
                set_worked = array->Set(context, i, array).FromJust();
            else
                set_worked = array->Set(context, i, js_from_py(item, context)).FromJust();
            assert(set_worked);
            Py_DECREF(item);
        }
        return hs.Escape(array);
    }
    if (PyFunction_Check_abi3(value) || PyMethod_Check_abi3(value))
    {
        py_function *templ = (py_function *)py_function_to_template(value);
        return hs.Escape(py_template_to_function(templ, context));
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
                context->Global()->Set(context, v8_name, object_template->NewInstance(context).ToLocalChecked());
                // templ->to_obj += 1;
            }
            Local<Value> obj = context->Global()->Get(context, v8_name).ToLocalChecked();
            ctx_c->hook = hook;
            return hs.Escape(obj);
        }
        return hs.Escape(py_class_get_constructor(templ, context));
    }
    if (PyObject_TypeCheck(value, js_object_type))
    {
        js_object *py_value = (js_object *)value;
        return hs.Escape(py_value->object.Get(isolate));
    }
    context_c *ctx_c = (context_c *)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (Py_TYPE(value) == Py_TYPE(ctx_c->py_global))
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
    if (PyInstance_Check(value))
    {
        type = PyObject_GetAttrString(value, "__class__");
    }
    else
    {
        type = (PyObject *)Py_TYPE(value);
        Py_INCREF(type);
    }
    py_class *templ = (py_class *)py_class_to_template(type, ctx_c);
    Py_DECREF(type);
    return hs.Escape(py_class_create_js_object(templ, value, context));
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
    const char *exc_message = PyUnicode_AsUTF8_abi3(exc_value_str);
    Py_XDECREF(exc_value_str);
    PyObject *exc_type = PyObject_Type(value);
    PyObject *exc_cls_name = PyObject_GetAttrString(exc_type, "__name__");
    Py_XDECREF(exc_type);
    const char *exc_type_name = PyUnicode_AsUTF8_abi3(exc_cls_name);
    Py_XDECREF(exc_value_str);
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
        exception->SetInternalField(2, External::New(isolate, exc_type));
        exception->Set(context, V8String(isolate, "message"), V8String(isolate, exc_message));
    }
    return exception;
}
