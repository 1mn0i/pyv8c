#include "bridge_base.h"
#include "error_family.h"

#include "convert.h"
#include "handle_resource.h"
#include "js_exception.h"
#include "runtime.h"
#include "trace.h"

#include <cstdarg>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

using namespace v8;

namespace error_family {
namespace {

//: The family, in publication order: the only copy of these names.
struct Member
{
    const char* js_name;
    const char* python_name;
    const char* type_name;  // dotted: the prefix names the type's module
    PyObject* builtin;      // borrowed; null derives from the root alone
};

const Member kMembers[] = {
    {"Error", "JsError", "pyv8.JsError", nullptr},
    {"TypeError", "JsTypeError", "pyv8.JsTypeError", PyExc_TypeError},
    {"RangeError", "JsRangeError", "pyv8.JsRangeError", PyExc_ValueError},
    {"ReferenceError", "JsReferenceError", "pyv8.JsReferenceError", PyExc_ReferenceError},
    {"SyntaxError", "JsSyntaxError", "pyv8.JsSyntaxError", PyExc_SyntaxError},
    {"URIError", "JsURIError", "pyv8.JsURIError", PyExc_ValueError},
    {"EvalError", "JsEvalError", "pyv8.JsEvalError", nullptr},
};
constexpr int kFamilySize = 7;

//: A member's position in *kMembers* is its ``Kind``, so the two must stay in step; the
//: host-facing API names members by kind and never repeats the table's contents.
static_assert(static_cast<int>(Kind::EvalError) + 1 == kFamilySize,
              "Kind needs one member per row of kMembers, in the same order");

PyTypeObject* family[kFamilySize] = {};
PyTypeObject* interceptor_marker = nullptr;

//: How a class whose realm constructor is not a standard name says so:
//: ``(constructor, argument attributes, (python attribute, JavaScript property) pairs)``.
constexpr const char* kShapeAttribute = "__v8_error_shape__";

//: How one instance says its realm should read no stack: ``__v8_error_stack__ = False``.
constexpr const char* kStackAttribute = "__v8_error_stack__";

//: What *thing* declares under *attribute*, or null with the reason cleared: a malformed
//: shape is passed over rather than raised, since an exception is already on its way out.
PyObject* declared(PyObject* thing, const char* attribute)
{
    PyObject* value = PyObject_GetAttrString(thing, attribute);
    if (value == nullptr)
    {
        PyErr_Clear();
        return nullptr;
    }
    return value;
}

//: The first of *members* in *exc_type*'s MRO: the most specific one answers.
PyTypeObject* first_in_mro(PyTypeObject* exc_type, PyTypeObject* const* members, int count)
{
    // The limited API leaves the type opaque, so the MRO is read as the attribute a
    // class publishes rather than out of the type struct.
    PyObject* mro = declared((PyObject*)exc_type, "__mro__");
    if (mro == nullptr)
        return nullptr;
    const Py_ssize_t size = PyTuple_Check(mro) ? PyTuple_Size(mro) : 0;
    PyTypeObject* found = nullptr;
    for (Py_ssize_t index = 0; index < size && found == nullptr; ++index)
    {
        PyObject* base = PyTuple_GetItem(mro, index);
        if (base == nullptr)
        {
            PyErr_Clear();
            break;
        }
        for (int member = 0; member < count; ++member)
        {
            if (members[member] == (PyTypeObject*)base)
            {
                found = members[member];
                break;
            }
        }
    }
    Py_DECREF(mro);
    return found;
}

//: The attribute names *sequence* lists; a missing or unusable one reads as none.
void read_arguments(PyObject* sequence, std::vector<std::string>& out)
{
    PyObject* iterator = PyObject_GetIter(sequence);
    if (iterator != nullptr)
    {
        PyObject* name;
        while ((name = PyIter_Next(iterator)) != nullptr)
        {
            const char* text = PyUnicode_Check(name) ? PyUnicode_AsUTF8_abi3(name) : nullptr;
            if (text != nullptr)
                out.emplace_back(text);
            Py_DECREF(name);
        }
        Py_DECREF(iterator);
    }
    PyErr_Clear();
}

//: The (python attribute, JavaScript property) pairs *sequence* lists; a missing or
//: unusable one reads as none.
void read_fields(PyObject* sequence, std::vector<std::pair<std::string, std::string>>& out)
{
    PyObject* iterator = PyObject_GetIter(sequence);
    if (iterator != nullptr)
    {
        PyObject* pair;
        while ((pair = PyIter_Next(iterator)) != nullptr)
        {
            PyObject* from = PySequence_Check(pair) && PySequence_Size(pair) == 2
                ? PySequence_GetItem(pair, 0) : nullptr;
            PyObject* to = PySequence_Check(pair) && PySequence_Size(pair) == 2
                ? PySequence_GetItem(pair, 1) : nullptr;
            const char* from_text = from != nullptr && PyUnicode_Check(from)
                ? PyUnicode_AsUTF8_abi3(from) : nullptr;
            const char* to_text = to != nullptr && PyUnicode_Check(to)
                ? PyUnicode_AsUTF8_abi3(to) : nullptr;
            if (from_text != nullptr && to_text != nullptr)
                out.emplace_back(from_text, to_text);
            Py_XDECREF(from);
            Py_XDECREF(to);
            Py_DECREF(pair);
        }
        Py_DECREF(iterator);
    }
    PyErr_Clear();
}

//: The shape *exc_type* declares, as the pieces build() needs; a missing or malformed one
//: reads as the family's own answer.  *shown* is meaningful only with *declared_name*.
void read_shape(PyTypeObject* exc_type, std::string& shown, bool& declared_name,
                std::vector<std::string>& arguments,
                std::vector<std::pair<std::string, std::string>>& fields)
{
    declared_name = false;
    PyObject* shape = declared((PyObject*)exc_type, kShapeAttribute);
    if (shape == nullptr)
        return;
    if (PySequence_Check(shape) && PySequence_Size(shape) == 3)
    {
        PyObject* name = PySequence_GetItem(shape, 0);
        PyObject* listed_arguments = PySequence_GetItem(shape, 1);
        PyObject* listed_fields = PySequence_GetItem(shape, 2);
        if (name != nullptr && PyUnicode_Check(name))
        {
            const char* text = PyUnicode_AsUTF8_abi3(name);
            if (text != nullptr)
            {
                shown.assign(text);
                declared_name = true;
            }
        }
        if (listed_arguments != nullptr)
            read_arguments(listed_arguments, arguments);
        if (listed_fields != nullptr)
            read_fields(listed_fields, fields);
        Py_XDECREF(name);
        Py_XDECREF(listed_arguments);
        Py_XDECREF(listed_fields);
    }
    PyErr_Clear();
    Py_DECREF(shape);
}

//: Creates the family types and the interceptor marker.  True when they stand.
bool initialize()
{
    if (family[0] != nullptr)
        return true;
    static PyType_Slot slots[] = {
        {Py_tp_doc, (void*)"a JavaScript error a realm may be shown"},
        {0, nullptr},
    };
    // One root, so one ``isinstance`` answers for the whole family, and each member beside
    // the builtin its error stands for.  The member comes first in the MRO, which is what
    // names the error.
    PyType_Spec root_spec = {kMembers[0].type_name, 0, 0,
                             Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, slots};
    PyObject* root = PyType_FromSpecWithBases(&root_spec, PyExc_Exception);
    if (root == nullptr)
        return false;
    family[0] = (PyTypeObject*)root;
    for (int member = 1; member < kFamilySize; ++member)
    {
        PyType_Spec spec = {kMembers[member].type_name, 0, 0,
                            Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, slots};
        PyObject* bases = kMembers[member].builtin == nullptr
            ? root : PyTuple_Pack(2, root, kMembers[member].builtin);
        if (bases == nullptr)
            return false;
        PyObject* type = PyType_FromSpecWithBases(&spec, bases);
        if (bases != root)
            Py_DECREF(bases);
        if (type == nullptr)
            return false;
        family[member] = (PyTypeObject*)type;
    }
    static PyType_Spec marker_spec = {"pyv8.JsInterceptorFailure", 0, 0,
                                      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, slots};
    PyObject* marker = PyType_FromSpecWithBases(&marker_spec, (PyObject*)PyExc_Exception);
    if (marker == nullptr)
        return false;
    interceptor_marker = (PyTypeObject*)marker;
    return true;
}

}  // namespace

//: The type a member is published as, or null while the family is not published yet.
PyTypeObject* member_type(Kind kind)
{
    const int index = static_cast<int>(kind);
    if (index < 0 || index >= kFamilySize)
        return nullptr;
    return family[index];
}

PyObject* make(Kind kind, const char* format, ...)
{
    PyTypeObject* type = member_type(kind);
    if (type == nullptr)
    {
        // No realm is there to read an error the family does not hold yet: the failure is
        // the host's own, and the wording says which one it refused.
        PyErr_SetString(PyExc_RuntimeError, format);
        return nullptr;
    }
    va_list arguments;
    va_start(arguments, format);
    PyObject* message = PyUnicode_FromFormatV(format, arguments);
    va_end(arguments);
    if (message == nullptr)
        return nullptr;
    PyObject* instance = PyObject_CallFunctionObjArgs((PyObject*)type, message, nullptr);
    Py_DECREF(message);
    return instance;
}

void raise(Kind kind, const char* format, ...)
{
    PyTypeObject* type = member_type(kind);
    if (type == nullptr)
    {
        PyErr_SetString(PyExc_RuntimeError, format);
        return;
    }
    va_list arguments;
    va_start(arguments, format);
    PyObject* message = PyUnicode_FromFormatV(format, arguments);
    va_end(arguments);
    if (message == nullptr)
        return;
    // The message, not an instance: ``e.args`` stays exactly what ``PyErr_Format`` left.
    PyErr_SetObject((PyObject*)type, message);
    Py_DECREF(message);
}

bool publish(PyObject* module)
{
    if (!initialize())
        return false;
    // The module keeps its own reference to each type, so they outlive it the way the
    // static ones do.
    for (int member = 0; member < kFamilySize; ++member)
    {
        if (PyModule_AddObjectRef(module, kMembers[member].python_name,
                                  (PyObject*)family[member]) < 0)
            return false;
    }
    return PyModule_AddObjectRef(module, "JsInterceptorFailure",
                                 (PyObject*)interceptor_marker) == 0;
}

const char* family_name(PyTypeObject* exc_type)
{
    if (exc_type == nullptr)
        return nullptr;
    PyTypeObject* member = first_in_mro(exc_type, family, kFamilySize);
    if (member == nullptr)
        return nullptr;
    for (int index = 0; index < kFamilySize; ++index)
        if (family[index] == member)
            return kMembers[index].js_name;
    return nullptr;
}

bool is_interceptor_failure(PyObject* exc_value)
{
    if (exc_value == nullptr || !PyExceptionInstance_Check(exc_value))
        return false;
    PyTypeObject* const members[] = {interceptor_marker};
    return interceptor_marker != nullptr
        && first_in_mro(Py_TYPE(exc_value), members, 1) != nullptr;
}

bool is_native_error_wrapper(Isolate* isolate, Local<Object> object)
{
    if (isolate == nullptr || object.IsEmpty())
        return false;
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (isolate_state == nullptr
        || object->InternalFieldCount() != OBJECT_INTERNAL_FIELDS)
        return false;
    // Only an object this binding made carries the marker, and only it has an owner field.
    if (object->GetInternalField(PYTHON_MAGIC_FIELD)
        != isolate_state->v8_string(V8StringId::PythonObjectMagic))
        return false;
    PyObject* owner = IsolateState::python_object(object);
    if (owner == nullptr)
    {
        PyErr_Clear();  // a closing context has no Python side left to ask
        return false;
    }
    return PyExceptionInstance_Check(owner) && family_name(Py_TYPE(owner)) != nullptr;
}

void attach_stack(Local<Context> context, Local<Object> error)
{
    if (context.IsEmpty() || error.IsEmpty())
        return;
    trace::TraceSilence silence(context_from_v8(context));
    Exception::CaptureStackTrace(context, error).FromMaybe(false);
}

bool carries_stack(PyObject* exc_value)
{
    PyObject* declared_stack = declared(exc_value, kStackAttribute);
    if (declared_stack == nullptr)
        return true;
    const int wanted = PyObject_IsTrue(declared_stack);
    Py_DECREF(declared_stack);
    return wanted != 0;
}

//: The realm's constructor for *shown*, from a source a page cannot rebind: the interface
//: this binding built for that name (a ``DOMException``), else nothing.
Local<Function> own_constructor(Isolate* isolate, Local<Context> context, const char* shown)
{
    return context_own_constructor(context, shown);
}

//: V8's own error of that name, made without asking the realm anything; empty for the two
//: names V8 offers no call for.
Local<Value> native_error(const char* shown, Local<String> message)
{
    if (std::strcmp(shown, "Error") == 0) return Exception::Error(message);
    if (std::strcmp(shown, "TypeError") == 0) return Exception::TypeError(message);
    if (std::strcmp(shown, "RangeError") == 0) return Exception::RangeError(message);
    if (std::strcmp(shown, "SyntaxError") == 0) return Exception::SyntaxError(message);
    if (std::strcmp(shown, "ReferenceError") == 0) return Exception::ReferenceError(message);
    return Local<Value>();
}

//: What the realm's global offers under that name: the last resort, and the only source a
//: page could have rebound.
Local<Value> global_function(Isolate* isolate, Local<Context> context, const char* shown)
{
    Local<Value> value;
    // A page may replace this fallback with a throwing accessor.  Its failure
    // must not leave an empty Local to dereference or replace the host error.
    TryCatch lookup(isolate);
    if (!context.IsEmpty() && !context->Global().IsEmpty())
        context->Global()->Get(context, V8String(isolate, shown)).ToLocal(&value);
    if (lookup.HasCaught())
        lookup.Reset();
    PyErr_Clear();
    return value;
}

//: A piece the shape promised and the value could not deliver: the host's own mistake, said
//: where the host can see it, and the error is built without it.
void report_piece(const char* what, const char* name)
{
    PySys_WriteStderr("pyv8: error %s '%s' could not cross\n", what, name);
}

Local<Object> build(Isolate* isolate, Local<Context> context, const char* name,
                    PyObject* exc_value, const char* message)
{
    // This is the host's conversion of a Python failure into a realm error.
    // Constructing it and attaching its fields/stack is not a page operation.
    trace::TraceSilence silence(context_from_v8(context));
    // A detached realm builds nothing new: its own error class would run the embedder
    // side inside a realm whose document is gone, so the message rides a plain error.
    if (context_is_detached(context))
        return Exception::Error(V8String(isolate, message)).As<Object>();

    PyTypeObject* exc_type = Py_TYPE(exc_value);
    std::string shown = name;
    std::vector<std::string> arguments;
    std::vector<std::pair<std::string, std::string>> fields;
    bool declared_name = false;
    read_shape(exc_type, shown, declared_name, arguments, fields);

    const Local<String> js_message = V8String(isolate, message);
    Local<Object> error;
    Local<Function> constructor = own_constructor(isolate, context, shown.c_str());
    if (constructor.IsEmpty() && arguments.empty())
    {
        // Nothing beyond the message to hand a constructor: V8's own error of that name is
        // already the realm's, and a page cannot shape it.
        Local<Value> native = native_error(shown.c_str(), js_message);
        if (!native.IsEmpty())
            error = native.As<Object>();
    }
    if (error.IsEmpty())
    {
        if (constructor.IsEmpty())
        {
            Local<Value> offered = global_function(isolate, context, shown.c_str());
            if (!offered.IsEmpty() && offered->IsFunction())
                constructor = offered.As<Function>();
        }
        if (!constructor.IsEmpty() && constructor->IsConstructor())
        {
            std::vector<Local<Value>> built;
            built.push_back(js_message);
            for (const std::string& attribute : arguments)
            {
                // A piece that does not arrive leaves its place: dropping it would move
                // every argument after it.
                PyObject* value = PyObject_GetAttrString(exc_value, attribute.c_str());
                if (value == nullptr)
                {
                    report_piece("argument", attribute.c_str());
                    PyErr_Clear();
                    built.push_back(V8String(isolate, ""));
                    continue;
                }
                Local<Value> js_value = js_from_py(value, context);
                Py_DECREF(value);
                if (js_value.IsEmpty())
                {
                    report_piece("argument", attribute.c_str());
                    PyErr_Clear();
                    built.push_back(V8String(isolate, ""));
                    continue;
                }
                built.push_back(js_value);
            }
            if (!constructor->NewInstance(context, (int)built.size(), built.data()).ToLocal(&error))
                PyErr_Clear();
        }
    }
    if (error.IsEmpty())
        error = Exception::Error(js_message).As<Object>();

    for (const auto& field : fields)
    {
        PyObject* value = PyObject_GetAttrString(exc_value, field.first.c_str());
        if (value == nullptr)
        {
            report_piece("field", field.first.c_str());
            PyErr_Clear();
            continue;
        }
        Local<Value> js_value = js_from_py(value, context);
        Py_DECREF(value);
        if (js_value.IsEmpty())
        {
            report_piece("field", field.first.c_str());
            PyErr_Clear();
            continue;
        }
        // Assignment follows the realm's own rules: a member the interface states read-only
        // (a DOMException's ``code``) is the constructor's to set, and is already set.
        bool set;
        error->Set(context, V8String(isolate, field.second.c_str()), js_value).To(&set);
    }
    // A realm-made error carries its own stack; one the host built here does not, so it gets
    // the stack of the crossing, as Blink gives the DOMExceptions it creates.
    if (!error.IsEmpty())
    {
        bool has_stack = false;
        if (error->HasOwnProperty(context, V8String(isolate, "stack")).To(&has_stack) && !has_stack)
            attach_stack(context, error);
    }
    return error;
}

Local<Object> exception_as_value(PyObject* value, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    Local<Object> exception;
    if (!PyExceptionInstance_Check(value))
    {
        return exception;
    }
    // A JavaScript exception stays the one it was, whichever way it travels.
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
        return exception;
    }
    // The family says what a realm may be shown: anything it does not contain stays
    // where it belongs, as the empty object the caller reads as "not a value JavaScript
    // was meant to answer with".
    const char* family = family_name(Py_TYPE(value));
    if (family == nullptr)
    {
        return exception;
    }
    PyObject *exc_value_str = pyexception_message(value);
    if (exc_value_str == NULL)
    {
        return exception;
    }
    const char *exc_message = PyUnicode_AsUTF8_abi3(exc_value_str);
    if (exc_message != NULL)
    {
        exception = build(isolate, context, family, value, exc_message);
    }
    Py_DECREF((PyObject*)exc_value_str);
    return exception;
}

}  // namespace error_family
