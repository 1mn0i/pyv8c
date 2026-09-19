// The error table, in both directions.
//
// The previous binding kept two hand written branch chains over the same set of
// names, one in each direction, so adding an error meant remembering to edit
// both.  Here one direction is a table and the other is a contract:
//
//   Python -> JavaScript: the exception names its JavaScript counterpart
//       through __v8_error_name__, falling back to its class name with the
//       binding's V8 prefix stripped.  pyv8c already declares its error classes
//       that way, so no list is duplicated in C++.
//
//       An exception whose JavaScript form carries more than a message says so
//       through __v8_error_args__, a sequence the constructor is called with
//       after the message.  A DOMException is the case that exists: its name is
//       the DOM's own, so `new DOMException(message, name)` is what produces the
//       object a page catches, and the binding cannot guess that from a class
//       name.
//
//   JavaScript -> Python: the host registers a mapping of JavaScript error name
//       to Python exception class (or to a callable taking name and message).
//       The binding ships no error classes of its own, because the host owns
//       the hierarchy and a second copy here would drift.
//
// Identity is preserved both ways: a JavaScript error that came from Python
// carries the original exception as a host object, so catching it in JavaScript
// and rethrowing it hands the same Python exception back.

#include "v8n/boundary.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/v8_compat.h"
#include "value/value_internal.h"
#include "v8n/realm.h"
#include "v8n/value.h"

#include <string>
#include <vector>

namespace v8n
{
namespace
{
// Owned by the module for the process lifetime.  Replaced wholesale rather than
// mutated, so a reader never sees a half updated map.
PyObject* registered_error_map = nullptr;

constexpr const char* kErrorNameAttribute = "__v8_error_name__";
constexpr const char* kErrorArgsAttribute = "__v8_error_args__";
constexpr const char* kOriginalErrorKey = "__python_error__";
constexpr const char* kThrownValueAttribute = "__v8_thrown__";
//: How many values an exception may add to its own error's construction.  Small
//: on purpose: this is for a constructor's parameters, not a place to hang data.
constexpr int kMaxErrorArguments = 4;

v8::Local<v8::String>
literal(v8::Isolate* isolate, const char* text)
{
    v8::Local<v8::String> result;
    v8::String::NewFromUtf8(isolate, text, v8::NewStringType::kInternalized).ToLocal(&result);
    return result;
}

std::string
to_std_string(v8::Isolate* isolate, v8::Local<v8::Value> value)
{
    if (value.IsEmpty() || !value->IsString())
        return std::string();
    v8::String::Utf8Value utf8(isolate, value);
    if (*utf8 == nullptr)
        return std::string();
    return std::string(*utf8, utf8.length());
}

// The JavaScript constructor name a Python exception asks for.
std::string
javascript_name_of(PyObject* exception, PyObject* exception_type)
{
    PyRef declared(PyRef::adopt(PyObject_GetAttrString(exception, kErrorNameAttribute)));
    if (!declared)
    {
        PyErr_Clear();
        PyRef name(PyRef::adopt(PyObject_GetAttrString(exception_type, "__name__")));
        if (!name)
        {
            PyErr_Clear();
            return "Error";
        }
        const char* text = abi3::Utf8(name.get());
        if (text == nullptr)
        {
            PyErr_Clear();
            return "Error";
        }
        std::string result(text);
        // pyv8c names its one-to-one counterparts V8Error, V8TypeError and so
        // on; the prefix is the binding's, not JavaScript's.
        if (result.rfind("V8", 0) == 0 && result.size() > 2)
            result.erase(0, 2);
        return result;
    }

    const char* text = abi3::Utf8(declared.get());
    if (text == nullptr)
    {
        PyErr_Clear();
        return "Error";
    }
    return std::string(text);
}

// The values an exception adds to its own error's construction, after the
// message.  A missing attribute is the ordinary case and means none.
std::vector<v8::Local<v8::Value>>
error_arguments(PyObject* error, Realm& realm)
{
    std::vector<v8::Local<v8::Value>> result;

    PyRef declared(PyRef::adopt(PyObject_GetAttrString(error, kErrorArgsAttribute)));
    if (!declared)
    {
        PyErr_Clear();
        return result;
    }

    // Sized and indexed through the exported functions: the fast macros that
    // read a sequence directly are hidden by the limited API.
    const Py_ssize_t count = PySequence_Size(declared.get());
    if (count < 0)
    {
        PyErr_Clear();
        return result;
    }

    for (Py_ssize_t index = 0; index < count && index < kMaxErrorArguments; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(declared.get(), index)));
        if (!item)
        {
            PyErr_Clear();
            result.clear();
            return result;
        }
        v8::Local<v8::Value> converted = to_js(item.get(), realm);
        if (converted.IsEmpty())
        {
            PyErr_Clear();
            result.clear();
            return result;
        }
        result.push_back(converted);
    }
    return result;
}

// Call the global constructor named `name` with `message` and whatever else the
// exception asked for.  Reports whether it worked, so the caller can fall back
// to a plain Error.
v8::Local<v8::Value>
construct_error(v8::Isolate* isolate, v8::Local<v8::Context> context,
                const std::string& name, const std::string& message,
                const std::vector<v8::Local<v8::Value>>& extra, bool* constructed)
{
    *constructed = false;

    v8::Local<v8::Value> constructor;
    if (!context->Global()->Get(context, literal(isolate, name.c_str())).ToLocal(&constructor))
        return v8::Local<v8::Value>();
    if (!constructor->IsFunction())
        return v8::Local<v8::Value>();

    v8::Local<v8::String> text;
    if (!v8::String::NewFromUtf8(isolate, message.c_str(), v8::NewStringType::kNormal)
             .ToLocal(&text))
        return v8::Local<v8::Value>();

    constexpr int kInlineArguments = 8;
    v8::Local<v8::Value> inline_arguments[kInlineArguments];
    const int count = 1 + static_cast<int>(extra.size());
    inline_arguments[0] = text;
    for (std::size_t index = 0; index < extra.size(); ++index)
        inline_arguments[index + 1] = extra[index];

    v8::Local<v8::Object> error;
    if (!constructor.As<v8::Function>()
             ->NewInstance(context, count < kInlineArguments ? count : kInlineArguments,
                           inline_arguments)
             .ToLocal(&error))
        return v8::Local<v8::Value>();

    *constructed = true;
    return error;
}

// Remember the JavaScript value a Python exception stands for.
//
// A conversion that runs a page's own code -- `String(value)`, which calls its
// `toString` -- can be made to throw, and what it throws crosses into Python as
// an exception like any other.  When that exception is on its way back out, the
// page is entitled to see the very value it threw: `catch (error) { error ===
// sentinel }` is a comparison of identity, and a new Error built from the text
// would be a different object.
//
// The value is kept the way any other value crosses: a primitive becomes the
// Python primitive, and an object or a symbol becomes a handle, which the realm
// releases when it closes.  A handle that has outlived its realm cannot be
// converted back, and the caller falls back to describing the error.
void remember_thrown_value(PyObject* instance, v8::Local<v8::Value> exception,
                           Realm& realm)
{
    if (instance == nullptr)
        return;
    PyRef converted(PyRef::adopt(to_python_boundary(exception, realm)));
    if (!converted)
    {
        PyErr_Clear();
        return;
    }
    if (PyObject_SetAttrString(instance, kThrownValueAttribute, converted.get()) < 0)
        PyErr_Clear();
}

// The value a Python exception remembers having been thrown as, if any.
v8::Local<v8::Value>
remembered_thrown_value(PyObject* error, Realm& realm)
{
    PyRef thrown(PyRef::adopt(PyObject_GetAttrString(error, kThrownValueAttribute)));
    if (!thrown)
    {
        PyErr_Clear();
        return v8::Local<v8::Value>();
    }
    v8::Local<v8::Value> converted = to_js(thrown.get(), realm);
    if (converted.IsEmpty())
        PyErr_Clear();
    return converted;
}

// Read the property that carries a Python exception back across the boundary.
bool
original_python_error(v8::Local<v8::Object> object, v8::Isolate* isolate,
                      v8::Local<v8::Context> context, Realm& realm, PyObject** original)
{
    *original = nullptr;

    v8::Local<v8::Value> stored;
    if (!object->Get(context, literal(isolate, kOriginalErrorKey)).ToLocal(&stored))
        return false;

    PythonOwner* record = nullptr;
    if (!python_owner_of(stored, realm, &record))
        return false;
    *original = record->instance;
    return true;
}
}

PyObject*
error_map()
{
    return registered_error_map;
}

bool
set_error_map(PyObject* mapping)
{
    if (mapping != nullptr && mapping != Py_None && !PyMapping_Check(mapping))
    {
        raise(PyExc_TypeError, "the error map must be a mapping of name to exception");
        return false;
    }

    PyObject* replacement = mapping == Py_None ? nullptr : mapping;
    Py_XINCREF(replacement);
    Py_XDECREF(registered_error_map);
    registered_error_map = replacement;
    return true;
}

v8::Local<v8::Value>
python_error_to_js(v8::Isolate* isolate)
{
    PyObject* error_type = nullptr;
    PyObject* error_value = nullptr;
    PyObject* error_traceback = nullptr;
    PyErr_Fetch(&error_type, &error_value, &error_traceback);
    PyErr_NormalizeException(&error_type, &error_value, &error_traceback);

    PyRef type(PyRef::adopt(error_type));
    PyRef value(PyRef::adopt(error_value));
    PyRef traceback(PyRef::adopt(error_traceback));

    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    if (context.IsEmpty())
        return v8::Local<v8::Value>();

    Realm* owner = Realm::from_context(context);

    // An exception that remembers a JavaScript value goes back out as that value,
    // which is what makes a conversion the page's own code threw into a round trip
    // rather than a translation.  See remember_thrown_value.
    if (owner != nullptr && value)
    {
        v8::Local<v8::Value> thrown = remembered_thrown_value(value.get(), *owner);
        if (!thrown.IsEmpty())
            return thrown;
    }

    std::string message = "Python exception";
    if (value)
    {
        PyRef text(PyRef::adopt(PyObject_Str(value.get())));
        if (text)
        {
            const char* rendered = abi3::Utf8(text.get());
            if (rendered != nullptr)
                message = rendered;
        }
        if (PyErr_Occurred())
            PyErr_Clear();
    }

    std::string name = "Error";
    if (type)
    {
        name = javascript_name_of(value ? value.get() : type.get(), type.get());
        if (PyErr_Occurred())
            PyErr_Clear();
    }

    // The extras are read through the realm, because converting them is the same
    // crossing as any other value the host hands to JavaScript.
    std::vector<v8::Local<v8::Value>> extra;
    if (owner != nullptr && value)
        extra = error_arguments(value.get(), *owner);

    bool constructed = false;
    v8::Local<v8::Value> error =
        construct_error(isolate, context, name, message, extra, &constructed);
    if (!constructed)
    {
        if (PyErr_Occurred())
            PyErr_Clear();
        // A host that raised before installing its error classes still gets a
        // throwable, just not a shaped one.
        v8::Local<v8::String> text;
        if (!v8::String::NewFromUtf8(isolate, message.c_str(), v8::NewStringType::kNormal)
                 .ToLocal(&text))
            return v8::Local<v8::Value>();
        error = v8::Exception::Error(text);
    }

    // Keep the Python exception reachable from the JavaScript error.  It is
    // stored as a host object, so the existing ownership machinery keeps the
    // instance alive while JavaScript can reach it and releases it afterwards.
    Realm* realm = Realm::from_context(context);
    if (realm != nullptr && value)
    {
        v8::Local<v8::Value> original = wrap_python_object(*realm, value.get());
        if (original.IsEmpty())
        {
            PyErr_Clear();
        }
        else if (!error.As<v8::Object>()
                      ->Set(context, literal(isolate, kOriginalErrorKey), original)
                      .FromMaybe(false))
        {
            PyErr_Clear();
        }
    }

    return error;
}

void
js_exception_to_python(v8::Local<v8::Value> exception, Realm& realm)
{
    v8::Isolate* isolate = realm.isolate().v8();
    v8::Local<v8::Context> context = realm.context();
    if (exception.IsEmpty() || context.IsEmpty())
    {
        raise(PyExc_RuntimeError, "a JavaScript exception with no value was raised");
        return;
    }

    std::string name = "Error";
    std::string message;

    if (exception->IsObject())
    {
        v8::Local<v8::Object> object = exception.As<v8::Object>();

        // A Python exception that travelled into JavaScript comes back as
        // itself, which is what lets a JavaScript catch and rethrow be
        // transparent to Python.
        PyObject* original = nullptr;
        if (original_python_error(object, isolate, context, realm, &original))
        {
            PyErr_SetObject(reinterpret_cast<PyObject*>(Py_TYPE(original)), original);
            return;
        }
        if (PyErr_Occurred())
            PyErr_Clear();

        v8::Local<v8::Value> property;
        if (object->Get(context, literal(isolate, "name")).ToLocal(&property))
            name = to_std_string(isolate, property);
        if (name.empty())
            name = "Error";
        if (object->Get(context, literal(isolate, "message")).ToLocal(&property))
            message = to_std_string(isolate, property);
        if (PyErr_Occurred())
            PyErr_Clear();
    }

    if (message.empty())
    {
        v8::String::Utf8Value rendered(isolate, exception);
        if (*rendered != nullptr)
            message.assign(*rendered, rendered.length());
    }
    if (message.empty())
        message = name;

    if (registered_error_map != nullptr)
    {
        PyRef key(PyRef::adopt(PyUnicode_FromStringAndSize(
            name.c_str(), static_cast<Py_ssize_t>(name.size()))));
        if (!key)
            return;

        PyRef entry(PyRef::adopt(PyDict_GetItemWithError(registered_error_map, key.get())));
        if (entry)
        {
            PyRef text(PyRef::adopt(PyUnicode_FromStringAndSize(
                message.c_str(), static_cast<Py_ssize_t>(message.size()))));
            if (!text)
                return;

            // A class is constructed with the message; a callable is given the
            // name as well, which is what the DOMException case needs.
            const bool is_class = PyType_Check(entry.get()) != 0;
            PyRef instance(PyRef::adopt(
                is_class ? PyObject_CallFunctionObjArgs(entry.get(), text.get(), nullptr)
                         : PyObject_CallFunctionObjArgs(entry.get(), key.get(), text.get(),
                                                        nullptr)));
            if (!instance)
                return;

            remember_thrown_value(instance.get(), exception, realm);
            PyErr_SetObject(is_class ? entry.get()
                                     : reinterpret_cast<PyObject*>(Py_TYPE(instance.get())),
                            instance.get());
            return;
        }
        if (PyErr_Occurred())
            PyErr_Clear();
    }

    if (exception->IsObject() && python_owner_of(exception, realm, nullptr))
    {
        // A host object was thrown directly.  Raising it as-is keeps the
        // identity a Python caller expects from `except SomeError`.
        PyObject* converted = to_python_boundary(exception, realm);
        if (converted == nullptr)
            return;
        PyRef instance(PyRef::adopt(converted));
        PyErr_SetObject(reinterpret_cast<PyObject*>(Py_TYPE(converted)), converted);
        return;
    }

    // No mapping entry and nothing to unwrap: say what JavaScript said rather
    // than inventing a class the host never declared.  The value is still
    // remembered, so a caller that lets the exception through hands JavaScript
    // back the value JavaScript threw.
    PyRef text(PyRef::adopt(
        PyUnicode_FromFormat("%s: %s", name.c_str(), message.c_str())));
    PyRef error(PyRef::adopt(
        text ? PyObject_CallFunctionObjArgs(reinterpret_cast<PyObject*>(PyExc_RuntimeError),
                                            text.get(), nullptr)
             : nullptr));
    if (!error)
    {
        PyErr_Clear();
        PyErr_Format(PyExc_RuntimeError, "%s: %s", name.c_str(), message.c_str());
        return;
    }
    remember_thrown_value(error.get(), exception, realm);
    PyErr_SetObject(PyExc_RuntimeError, error.get());
}

void
throw_python_error(v8::Isolate* isolate)
{
    if (isolate == nullptr)
        return;

    v8::Local<v8::Value> error = python_error_to_js(isolate);
    if (!error.IsEmpty())
    {
        isolate->ThrowException(error);
        return;
    }

    // Translation failed and left a Python error behind.  JavaScript still has
    // to be told that the call failed, because a callback that returns normally
    // after an error is indistinguishable from one that succeeded.  The Python
    // error was fetched and is not raised anywhere, so it is dropped here
    // rather than left to surface in the next unrelated Python statement.
    PyErr_Clear();
    isolate->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8Literal(
        isolate,
        "a Python error crossed into JavaScript and could not be translated")));
}

}  // namespace v8n
