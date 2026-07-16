#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "jsobject.h"
#include "jsvalue.h"
#include "context.h"

#include <climits>
#include <new>
#include <unordered_map>
#include <vector>

namespace
{
class JsToPyConversionContext
{
public:
    explicit JsToPyConversionContext(Isolate* isolate)
        : isolate_(isolate)
    {
    }

    PyObject* find(Local<Object> object)
    {
        for (const Entry& entry : visited_)
        {
            if (entry.object.Get(isolate_)->StrictEquals(object))
            {
                Py_INCREF(entry.result);
                return entry.result;
            }
        }
        return NULL;
    }

    bool remember(Local<Object> object, PyObject* result)
    {
        try
        {
            visited_.emplace_back(isolate_, object, result);
        }
        catch (const std::bad_alloc&)
        {
            PyErr_NoMemory();
            return false;
        }
        return true;
    }

private:
    struct Entry
    {
        Entry(Isolate* isolate, Local<Object> object, PyObject* result)
            : object(isolate, object), result(result)
        {
        }

        Entry(Entry&&) noexcept = default;
        Entry& operator=(Entry&&) noexcept = default;
        Entry(const Entry&) = delete;
        Entry& operator=(const Entry&) = delete;

        Global<Object> object;
        PyObject* result;
    };

    std::vector<Entry> visited_;
    Isolate* isolate_;
};

class PyToJsConversionContext
{
public:
    explicit PyToJsConversionContext(Isolate* isolate)
        : inline_size_(0), isolate_(isolate)
    {
    }

    bool find(PyObject* object, Local<Value>* result) const
    {
        for (size_t i = 0; i < inline_size_; ++i)
        {
            if (inline_objects_[i] == object)
            {
                *result = inline_values_[i];
                return true;
            }
        }
        auto item = overflow_.find(object);
        if (item != overflow_.end())
        {
            *result = item->second.Get(isolate_);
            return true;
        }
        return false;
    }

    bool remember(PyObject* object, Local<Value> result)
    {
        if (inline_size_ < kInlineCapacity)
        {
            inline_objects_[inline_size_] = object;
            inline_values_[inline_size_] = result;
            ++inline_size_;
            return true;
        }
        try
        {
            overflow_.emplace(object, Global<Value>(isolate_, result));
        }
        catch (const std::bad_alloc&)
        {
            PyErr_NoMemory();
            return false;
        }
        return true;
    }

private:
    static constexpr size_t kInlineCapacity = 16;
    size_t inline_size_;
    PyObject* inline_objects_[kInlineCapacity];
    Local<Value> inline_values_[kInlineCapacity];
    std::unordered_map<PyObject*, Global<Value>> overflow_;
    Isolate* isolate_;
};
}

static PyObject *
py_from_js_impl(Local<Value> value,
                Local<Context> context,
                JsToPyConversionContext* conversion)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (value->IsSymbol())
    {
        value = value.As<Symbol>()->Description(v8_compat::context_isolate(context));
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
        JsToPyConversionContext local_conversion(isolate);
        if (conversion == nullptr)
            conversion = &local_conversion;

        Local<Array> array = value.As<Array>();
        PyObject* existing = conversion->find(array);
        if (existing != NULL || PyErr_Occurred())
            return existing;

        const uint32_t length = array->Length();
        PyObject *list = PyList_New(length);
        if (!list)
            return NULL;
        if (!conversion->remember(array, list))
        {
            Py_DECREF(list);
            return NULL;
        }

        for (uint32_t i = 0; i < length; i++)
        {
            Local<Value> js_value;
            if (!array->Get(context, i).ToLocal(&js_value))
            {
                Py_DECREF(list);
                return NULL;
            }

            PyObject *obj = py_from_js_impl(js_value, context, conversion);
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
        Maybe<bool> maybe_has_to_string_tag = obj_value->HasOwnProperty(context, Symbol::GetToStringTag(v8_compat::context_isolate(context)));
        if (!maybe_has_to_string_tag.To(&has_to_string_tag))
        {
            PyErr_SetString(PyExc_RuntimeError, "failed to inspect JavaScript object");
            return NULL;
        }
        if (obj_value->GetPrototype()->StrictEquals(context->GetEmbedderData(OBJECT_PROTOTYPE_SLOT)) && !has_to_string_tag)
        {
            JsToPyConversionContext local_conversion(isolate);
            if (conversion == nullptr)
                conversion = &local_conversion;

            PyObject* existing = conversion->find(obj_value);
            if (existing != NULL || PyErr_Occurred())
                return existing;

            PyObject *dict = PyDict_New();
            if (!dict)
                return NULL;
            if (!conversion->remember(obj_value, dict))
            {
                Py_DECREF(dict);
                return NULL;
            }

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
                PyObject *key = py_from_js_impl(js_key, context, conversion);
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
                PyObject *dict_value = py_from_js_impl(js_value, context, conversion);
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
            Local<Data> magic = obj_value->GetInternalField(PYTHON_MAGIC_FIELD);
            if (magic == isolate_state->v8_string(V8StringId::PythonObjectMagic))
            {
                PyObject *object = (PyObject *)v8_compat::external_value(obj_value->GetInternalField(PYTHON_OBJECT_FIELD));
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

PyObject *
py_from_js(Local<Value> value, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    IN_ISOLATE(isolate_state);
    return py_from_js_impl(value, context, nullptr);
}

static Local<Value>
js_from_py_impl(PyObject *value,
                 Local<Context> context,
                 PyToJsConversionContext* conversion,
                 Isolate* isolate)
{
    if (value == Py_False)
    {
        return False(isolate);
    }
    if (value == Py_True)
    {
        return True(isolate);
    }
    if (value == js_undefined_object)
    {
        return Undefined(isolate);
    }
    if (value == Py_None)
    {
        return Undefined(isolate);
    }
    if (value == js_null_object)
    {
        return Null(isolate);
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
        return js_value;
    }
    if (PyBytes_Check(value))
    {
        char *str;
        Py_ssize_t len;
        if (PyBytes_AsStringAndSize(value, &str, &len) < 0)
        {
            return Undefined(isolate);
        }
        Local<ArrayBuffer> js_value = ArrayBuffer::New(isolate, static_cast<size_t>(len));
        if (len > 0)
        {
            memcpy(js_value->Data(), str, static_cast<size_t>(len));
        }
        return js_value;
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
            return Undefined(isolate);
        }
        return js_value;
    }

    if (PyDict_Check(value))
    {
        PyToJsConversionContext local_conversion(isolate);
        if (conversion == nullptr)
            conversion = &local_conversion;

        Local<Value> existing;
        if (conversion->find(value, &existing))
            return existing;

        // a context scope is (I think) needed for Object::New to work
        Context::Scope cs(context);
        Local<Object> js_dict = Object::New(isolate);
        if (!conversion->remember(value, js_dict))
            return Local<Value>();

        PyObject *dict = value;
        PyObject *key, *dict_value;
        Py_ssize_t pos = 0;
        while (PyDict_Next(dict, &pos, &key, &dict_value))
        {
            Local<Value> js_key = js_from_py_impl(key, context, conversion, isolate);
            if (js_key.IsEmpty() || PyErr_Occurred())
            {
                return Local<Value>();
            }
            Local<Value> js_value = js_from_py_impl(dict_value, context, conversion, isolate);
            if (js_value.IsEmpty() || PyErr_Occurred())
            {
                return Local<Value>();
            }
            bool set_worked;
            if (!js_dict->Set(context, js_key, js_value).To(&set_worked))
            {
                PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript object property");
                return Local<Value>();
            }
        }
        return js_dict;
    }

    if (PyList_Check(value) || PyTuple_Check(value))
    {
        PyToJsConversionContext local_conversion(isolate);
        if (conversion == nullptr)
            conversion = &local_conversion;

        Local<Value> existing;
        if (conversion->find(value, &existing))
            return existing;

        Py_ssize_t length = PySequence_Length(value);
        if (length < 0 || length > INT_MAX)
        {
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_OverflowError, "Python sequence is too large for a JavaScript array");
            return Local<Value>();
        }
        Local<Array> array = Array::New(isolate, static_cast<int>(length));
        if (!conversion->remember(value, array))
            return Local<Value>();

        for (Py_ssize_t i = 0; i < length; i++)
        {
            PyObject *item = PyList_Check(value)
                ? PyList_GetItem(value, i)
                : PyTuple_GetItem(value, i);
            if (item == NULL)
            {
                return Local<Value>();
            }
            Local<Value> js_item = js_from_py_impl(item, context, conversion, isolate);
            if (js_item.IsEmpty() || PyErr_Occurred())
                return Local<Value>();

            bool set_worked;
            if (!array->Set(context, static_cast<uint32_t>(i), js_item).To(&set_worked))
            {
                PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript array item");
                return Local<Value>();
            }
            assert(set_worked);
        }
        return array;
    }
    if (PyFunction_Check_abi3(value) || PyMethod_Check_abi3(value))
    {
        IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
        py_function *templ = (py_function *)py_function_to_template(value, isolate_state);
        if (templ == NULL)
        {
            return Local<Value>();
        }
        Local<Function> function = py_template_to_function(templ, context);
        if (function.IsEmpty())
        {
            return Local<Value>();
        }
        return function;
    }

    if (PyType_Check(value))
    {
        context_c *ctx_c = (context_c *)v8_compat::external_value(context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
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
            return obj;
        }
        Local<Function> constructor = py_class_get_constructor(context, templ, (py_class *)templ->parent);
        if (constructor.IsEmpty())
        {
            return Local<Value>();
        }
        return constructor;
    }
    if (PyObject_TypeCheck(value, js_object_type))
    {
        js_object *py_value = (js_object *)value;
        if (py_value->isolate_state == nullptr || py_value->isolate_state->isolate() != isolate)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
            return Local<Value>();
        }
        return py_value->object.Get(isolate);
    }

    if (PyObject_TypeCheck(value, js_value_type))
    {
        js_value* py_value = (js_value*)value;
        if (py_value->isolate_state == nullptr || py_value->isolate_state->isolate() != isolate)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
            return Local<Value>();
        }
        return py_value->value.Get(isolate);
    }

    // python global_this -> js globalThis
    context_c *ctx_c = (context_c *)v8_compat::external_value(context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
    if (ctx_c->py_global != NULL && Py_TYPE(value) == Py_TYPE(ctx_c->py_global))
    {
        PyObject *ctx = PyObject_GetAttrString(value, "_ctx");
        if (ctx == NULL)
        {
            if (PyErr_Occurred())
                PyErr_Clear();
            return context->Global();
        }

        context_c* source_context = (context_c*)ctx;
        if (source_context->isolate_state == nullptr || source_context->isolate_state->isolate() != isolate)
        {
            Py_DECREF(ctx);
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
            return Local<Value>();
        }
        auto js_content = source_context->js_context.Get(isolate);
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
    return js_object;
}

Local<Value>
js_from_py(PyObject *value, Local<Context> context)
{
    if (context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "an explicit isolate is required without a Context");
        return Local<Value>();
    }
    return js_from_py(value, context, v8_compat::context_isolate(context));
}

Local<Value>
js_from_py(PyObject *value, Local<Context> context, Isolate* target_isolate)
{
    if (!context.IsEmpty() && v8_compat::context_isolate(context) != target_isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return Local<Value>();
    }
    IsolateState* isolate_state = runtime_manager.find_isolate(target_isolate);
    if (isolate_state == nullptr || !isolate_state->require_active())
        return Local<Value>();
    ESCAPING_IN_ISOLATE(isolate_state);
    Local<Value> result = js_from_py_impl(value, context, nullptr, isolate);
    if (result.IsEmpty())
        return result;
    return hs.Escape(result);
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
    const Py_ssize_t length = PyTuple_Size(py_args);
    for (Py_ssize_t i = 0; i < length; i++)
    {
        js_args[i] = js_from_py(PyTuple_GetItem(py_args, i), context);
    }
}

Local<Object>
pyexception_to_jsexception(PyObject *value, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
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
        js_exception* py_exception = (js_exception*)value;
        if (py_exception->isolate_state != isolate_state)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        }
        else
        {
            exception = py_exception->exception.Get(isolate).As<Object>();
        }
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
        Local<Value> js_exception = js_from_py(value, context);
        if (!js_exception.IsEmpty() && js_exception->IsObject())
        {
            exception = js_exception.As<Object>();
            isolate_state->attach_exception_state(exception, exc_type, nullptr);
            exception->Set(
                context,
                isolate_state->v8_string(V8StringId::Message),
                V8String(isolate, exc_message));
        }
    }
    Py_XDECREF(exc_cls_name);
    Py_XDECREF(exc_type);
    Py_XDECREF(exc_value_str);
    return exception;
}
