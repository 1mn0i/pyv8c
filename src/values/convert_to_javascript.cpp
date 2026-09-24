#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "error_map.h"
#include "exception.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "jsobject.h"
#include "jsvalue.h"
#include "context.h"

#include <climits>
#include <new>
#include <unordered_map>

using namespace v8;

namespace
{
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
        if (len > INT_MAX)
        {
            PyErr_SetString(PyExc_OverflowError,
                            "Python string is too large for a V8 string");
            return Local<Value>();
        }
        const int length = static_cast<int>(len);
        // ASCII is what a page hands over most of the time, and UTF-8 spells it
        // one byte per code unit: V8 can take those bytes as they are.  The scan
        // is over the buffer CPython already cached and costs less than the decode.
        bool ascii = true;
        for (Py_ssize_t index = 0; index < len; ++index)
        {
            if ((static_cast<unsigned char>(str[index]) & 0x80) != 0)
            {
                ascii = false;
                break;
            }
        }
        Local<String> js_value;
        if (ascii)
        {
            if (!String::NewFromOneByte(
                    isolate, reinterpret_cast<const uint8_t*>(str),
                    NewStringType::kNormal, length).ToLocal(&js_value))
            {
                PyErr_SetString(PyExc_RuntimeError, "failed to create JavaScript string");
                return Local<Value>();
            }
            return js_value;
        }
        if (!String::NewFromUtf8(
                isolate, str, NewStringType::kNormal,
                length).ToLocal(&js_value))
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
            long long long_value = PyLong_AsLongLong(value);
            if (!PyErr_Occurred() &&
                long_value >= INT32_MIN && long_value <= INT32_MAX)
            {
                js_value = Integer::New(isolate, static_cast<int32_t>(long_value));
            }
            else
            {
                // JavaScript numbers can represent integers larger than Int32,
                // but Python conversion must still report values that cannot be
                // converted to a finite JavaScript Number.
                PyErr_Clear();
                double double_value = PyLong_AsDouble(value);
                if (PyErr_Occurred())
                    return Local<Value>();
                js_value = Number::New(isolate, double_value);
            }
        }
        else
        {
            // A Python number that is neither an int nor a float: Decimal and
            // Fraction land here.  JavaScript has no number to give them, and an
            // undefined that says nothing is worse than an error that says why.
            PyObject* kind = PyType_GetName(Py_TYPE(value));
            if (kind != nullptr) {
                PyErr_Format(PyExc_TypeError,
                             "cannot convert a Python %U to a JavaScript number", kind);
                Py_DECREF(kind);
            } else {
                PyErr_SetString(PyExc_TypeError,
                                "cannot convert a Python number to a JavaScript number");
            }
            return Local<Value>();
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
            // A property key, as JavaScript would take one: names as they are, and
            // anything else -- an int key, say -- by its string form.
            Local<Name> js_name;
            if (js_key->IsName())
            {
                js_name = js_key.As<Name>();
            }
            else
            {
                Local<String> text;
                if (!js_key->ToString(context).ToLocal(&text))
                {
                    PyErr_SetString(PyExc_RuntimeError, "failed to convert JavaScript property key");
                    return Local<Value>();
                }
                js_name = text;
            }

            bool set_worked;
            // Define rather than assign, as for array items: assigning would route a
            // "__proto__" key through Object.prototype's accessor and hand the data
            // control of the object's prototype.  A "no" that threw nothing is a
            // failed write too.
            if (!js_dict->CreateDataProperty(context, js_name, js_value).To(&set_worked) ||
                !set_worked)
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
            // Define rather than assign: building an array must not run a setter a
            // page put on Array.prototype, and a "no" that threw nothing is a failure.
            if (!array->CreateDataProperty(context, static_cast<uint32_t>(i), js_item)
                     .To(&set_worked) ||
                !set_worked)
            {
                PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript array item");
                return Local<Value>();
            }
        }
        return array;
    }
    if (PyFunction_Check_abi3(value) || PyMethod_Check_abi3(value))
    {
        auto* owner = context_from_v8(context);
        if (owner == nullptr) {
            PyErr_SetString(PyExc_RuntimeError, "V8 context is closed");
            return Local<Value>();
        }
        py_function *templ = (py_function *)py_function_to_template(value, owner);
        if (templ == NULL)
        {
            return Local<Value>();
        }
        PyOwnedRef keep_template((PyObject*)templ);
        Local<Function> function = py_template_to_function(templ, context);
        if (function.IsEmpty())
        {
            return Local<Value>();
        }
        return function;
    }

    if (PyType_Check(value))
    {
        context_c *ctx_c = context_from_v8(context);
        if (ctx_c == nullptr)
        {
            PyErr_SetString(PyExc_RuntimeError, "V8 context has no Python owner");
            return Local<Value>();
        }
        py_class *templ = (py_class *)py_class_to_template(value, ctx_c);
        if (templ == NULL)
        {
            return Local<Value>();
        }
        PyOwnedRef keep_template((PyObject*)templ);
        if (templ->to_obj)
        {
            const char *world = PyUnicode_AsUTF8_abi3(templ->cls_name);
            if (world == NULL)
            {
                return Local<Value>();
            }
            Local<String> v8_name = V8String(isolate, world);
            bool trace = ctx_c->trace;
            ctx_c->trace = false;
            if (templ->to_obj == 1)
            {
                v8::Local<v8::ObjectTemplate> object_template = templ->templ.Get(isolate).As<v8::ObjectTemplate>();
                Local<Object> new_instance;
                if (!object_template->NewInstance(context).ToLocal(&new_instance))
                {
                    ctx_c->trace = trace;
                    PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript object template");
                    return Local<Value>();
                }
                bool set_worked;
                if (!context->Global()->Set(context, v8_name, new_instance).To(&set_worked))
                {
                    ctx_c->trace = trace;
                    PyErr_SetString(PyExc_RuntimeError, "failed to expose JavaScript object template");
                    return Local<Value>();
                }
                // templ->to_obj += 1;
            }
            Local<Value> obj;
            if (!context->Global()->Get(context, v8_name).ToLocal(&obj))
            {
                ctx_c->trace = trace;
                PyErr_SetString(PyExc_RuntimeError, "failed to retrieve JavaScript object template");
                return Local<Value>();
            }
            ctx_c->trace = trace;
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
        if (!js_object_require_active(py_value)) return Local<Value>();
        if (py_value->isolate_state == nullptr || py_value->isolate_state->isolate() != isolate)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
            return Local<Value>();
        }
        if (!context_retain_dependency(context, py_value->py_context)) return Local<Value>();
        return py_value->object.Get(isolate);
    }

    if (PyObject_TypeCheck(value, js_value_type))
    {
        js_value* py_value = (js_value*)value;
        if (py_value->value.IsEmpty()) {
            PyErr_SetString(PyExc_RuntimeError, "V8 value context is closed");
            return Local<Value>();
        }
        if (py_value->isolate_state == nullptr || py_value->isolate_state->isolate() != isolate)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
            return Local<Value>();
        }
        if (!context_retain_dependency(context, py_value->py_context)) return Local<Value>();
        return py_value->value.Get(isolate);
    }

    // python global_this -> js globalThis
    context_c *ctx_c = context.IsEmpty() ? nullptr :
        context_from_v8(context);
    if (ctx_c == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Python objects require a V8 context");
        return Local<Value>();
    }
    if (ctx_c->py_global != NULL && Py_TYPE((PyObject*)value) == Py_TYPE(ctx_c->py_global))
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
            Py_DECREF((PyObject*)ctx);
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
            return Local<Value>();
        }
        auto js_content = source_context->js_context.Get(isolate);
        Py_DECREF((PyObject*)ctx);
        return js_content->Global();
    }
    // it's an arbitrary object
    PyObject *type;
    type = (PyObject*)Py_TYPE((PyObject*)value);
    py_class *templ = (py_class *)py_class_to_template(type, ctx_c);
    if (templ == NULL)
    {
        return Local<Value>();
    }
    PyOwnedRef keep_template((PyObject*)templ);
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
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        return Local<Value>();
    }
    if (auto* active = current_isolate_state(); active && active->isolate() == target_isolate)
        return js_from_py_impl(value, context, nullptr, target_isolate);
    IsolateState* isolate_state = runtime_manager.find_isolate(target_isolate);
    if (isolate_state == nullptr || !isolate_state->require_active())
        return Local<Value>();
    ESCAPING_IN_ISOLATE(isolate_state);
    Local<Value> result = js_from_py_impl(value, context, nullptr, isolate);
    if (result.IsEmpty())
        return result;
    return hs.Escape(result);
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
    PyObject *exc_value_str = pyexception_message(value);
    if (exc_value_str == NULL)
    {
        return exception;
    }
    const char *exc_message = PyUnicode_AsUTF8_abi3(exc_value_str);
    if (exc_message == NULL)
    {
        Py_XDECREF((PyObject*)exc_value_str);
        return exception;
    }
    PyObject *exc_type = PyObject_Type(value);
    if (exc_type == NULL)
    {
        Py_XDECREF((PyObject*)exc_value_str);
        return exception;
    }
    PyObject *exc_cls_name = PyObject_GetAttrString(exc_type, "__name__");
    if (exc_cls_name == NULL)
    {
        Py_XDECREF((PyObject*)exc_type);
        Py_XDECREF((PyObject*)exc_value_str);
        return exception;
    }
    const char *exc_type_name = PyUnicode_AsUTF8_abi3(exc_cls_name);
    if (exc_type_name == NULL)
    {
        Py_XDECREF((PyObject*)exc_cls_name);
        Py_XDECREF((PyObject*)exc_type);
        Py_XDECREF((PyObject*)exc_value_str);
        return exception;
    }
    // A class may have named itself with __v8_error_name__.  Both directions read
    // the same table, so the same class has to arrive as the same JavaScript
    // error whether it was thrown or used as a rejection reason.
    bool has_explicit_js_error_name = false;
    PyObject* js_error_name = PyObject_GetAttrString(value, "__v8_error_name__");
    if (js_error_name != nullptr && PyUnicode_Check(js_error_name))
    {
        const char* explicit_name = PyUnicode_AsUTF8_abi3(js_error_name);
        if (explicit_name != nullptr)
        {
            exc_type_name = explicit_name;
            has_explicit_js_error_name = true;
            Py_DECREF((PyObject*)exc_cls_name);
            exc_cls_name = js_error_name;
            js_error_name = nullptr;
        }
    }
    else if (js_error_name == nullptr && PyErr_Occurred())
    {
        PyErr_Clear();
    }
    Py_XDECREF((PyObject*)js_error_name);
    if (PyObject_TypeCheck(value, js_exception_type))
    {
        JsExceptionState* state = js_exception_get_state(value);
        if (state == nullptr)
        {
            PyErr_Clear();
            return exception;
        }
        if (state->isolate_state != isolate_state)
        {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        }
        else
        {
            exception = state->exception.Get(isolate).As<Object>();
        }
    }
    else if (Local<Object> mapped = error_map::js_error_for(
                 isolate, exc_type_name, exc_message, has_explicit_js_error_name);
             !mapped.IsEmpty())
    {
        exception = mapped;
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
    Py_XDECREF((PyObject*)exc_cls_name);
    Py_XDECREF((PyObject*)exc_type);
    Py_XDECREF((PyObject*)exc_value_str);
    return exception;
}
