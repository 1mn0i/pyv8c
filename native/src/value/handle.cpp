// The Python wrapper for a retained JavaScript value.
//
// One type covers every value.  Reading a property, writing one, and calling
// are all offered; calling a value that turns out not to be callable raises the
// same TypeError V8 would, rather than being a separate type a host has to
// know about before it can use the result.

#include "v8n/value.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "value/value_internal.h"
#include "v8n/boundary.h"
#include "v8n/realm.h"

#include <vector>

namespace v8n::value
{

PyTypeObject* value_type = nullptr;

namespace
{

ValueObject* self_of(PyObject* object)
{
    return reinterpret_cast<ValueObject*>(object);
}

// A handle is only usable while the realm that made it is open and the caller
// is on the owning thread.  Both failures are the host's to fix, so both are
// reported rather than silently producing undefined.
Realm* usable_realm(ValueObject* self, const char* operation)
{
    if (self->realm == nullptr || self->slot == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: this handle was released when its realm closed", operation);
        return nullptr;
    }
    if (!self->realm->is_active())
    {
        raise(PyExc_RuntimeError, "%s: the realm is closed", operation);
        return nullptr;
    }
    return self->realm;
}

int handle_traverse(ValueObject* self, visitproc visit, void* arg)
{
    VN_VISIT(self->realm_object);
    return 0;
}

int handle_clear(ValueObject* self)
{
    Py_CLEAR(self->realm_object);
    return 0;
}

void handle_dealloc(ValueObject* self)
{
    PyObject_GC_UnTrack(self);
    handle_clear(self);
    if (self->slot != nullptr)
    {
        // Releasing the handle can happen after the realm closed; the slot is
        // already detached then and reset() is a no-op.
        delete self->slot;
        self->slot = nullptr;
    }
    self->realm = nullptr;
    abi3::FreeInstance(value_type, self);
}

PyObject* handle_to_python(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "to_python()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::HandleScope handle_scope(entry.v8());
    v8::Context::Scope context_scope(entry.context());
    return to_python(self->slot->get(entry.v8()), *realm);
}

// Reading a property by name, for a caller that already knows the name is a
// JavaScript one.
//
// This is what `get()` is, and it exists because `getattr` cannot serve it: a
// Python attribute of the wrapper wins there, so a JavaScript object with a
// property called `to_python` would be unreachable.  The host object the
// conversion layer reads is arbitrary, so the safe form has to be explicit.
PyObject* read_property(ValueObject* self, const char* operation, PyObject* name)
{
    Realm* realm = usable_realm(self, operation);
    if (realm == nullptr)
        return nullptr;
    if (!PyUnicode_Check(name))
        return raise(PyExc_TypeError, "a JavaScript property name must be a string");

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Value> key = to_js(name, *realm);
    if (key.IsEmpty())
        return nullptr;

    v8::Local<v8::Value> receiver = self->slot->get(isolate);
    if (receiver.IsEmpty())
        return raise(PyExc_RuntimeError, "this handle was released when its realm closed");

    v8::TryCatch try_catch(isolate);
    v8::Local<v8::Value> result;
    if (!receiver->ToObject(context).ToLocal(&result))
    {
        // A primitive has no properties; JavaScript answers undefined rather
        // than failing, so the wrapper does too.
        PyErr_Clear();
        return PyRef::borrow(undefined_singleton()).release();
    }
    if (!result.As<v8::Object>()->Get(context, key).ToLocal(&result))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to read a JavaScript property");
        return nullptr;
    }

    PyObject* converted = to_python_boundary(result, *realm);
    realm->reap_owners();
    return converted;
}

// One call, shared by `value(*args)` and `value.call(receiver, *args)`: the
// only difference between them is the receiver, and having one implementation
// is what keeps the two from drifting on argument conversion or error mapping.
//
// The arguments are the tuple entries from *offset* on, so the caller can pass
// the same tuple it was handed without building a second one.
PyObject*
invoke(ValueObject* self, Realm& realm, v8::Isolate* isolate,
       v8::Local<v8::Context> context, v8::Local<v8::Value> receiver,
       PyObject* args, Py_ssize_t offset)
{
    const Py_ssize_t total = PyTuple_Size(args);
    if (total < 0)
        return nullptr;
    const Py_ssize_t count = total - offset;

    // JavaScript arguments are built on the stack for the common case and on
    // the heap beyond it; the previous binding allocated a vector every call.
    constexpr Py_ssize_t kInlineArguments = 8;
    v8::Local<v8::Value> inline_arguments[kInlineArguments];
    std::vector<v8::Local<v8::Value>> overflow;
    v8::Local<v8::Value>* arguments = inline_arguments;
    if (count > kInlineArguments)
    {
        overflow.resize(static_cast<size_t>(count));
        arguments = overflow.data();
    }

    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyObject* item = PyTuple_GetItem(args, index + offset);
        if (item == nullptr)
            return nullptr;
        arguments[index] = to_js(item, realm);
        if (arguments[index].IsEmpty())
            return nullptr;
    }

    v8::Local<v8::Value> callable = self->slot->get(isolate);
    if (!callable->IsFunction())
        return raise(PyExc_TypeError, "this JavaScript value is not callable");

    v8::TryCatch try_catch(isolate);
    v8::Local<v8::Value> result;
    if (!callable.As<v8::Function>()
             ->Call(context, receiver, static_cast<int>(count), arguments)
             .ToLocal(&result))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), realm);
        else
            raise(PyExc_RuntimeError, "the JavaScript call failed");
        return nullptr;
    }

    PyObject* converted = to_python_boundary(result, realm);
    realm.reap_owners();
    return converted;
}

// Reading an attribute forwards to JavaScript, which is what makes a handle
// usable without knowing anything about the object behind it.
PyObject* handle_getattro(ValueObject* self, PyObject* name)
{
    PyObject* found = PyObject_GenericGetAttr(reinterpret_cast<PyObject*>(self), name);
    if (found != nullptr)
        return found;
    PyErr_Clear();
    return read_property(self, "attribute access", name);
}

PyObject* handle_get(ValueObject* self, PyObject* name)
{
    return read_property(self, "get()", name);
}

// The value as an object, for the operations that are defined on objects only.
// A primitive is boxed, which is what JavaScript does when a property is read
// from one.
v8::Local<v8::Object>
receiver_object(ValueObject* self, v8::Isolate* isolate, v8::Local<v8::Context> context)
{
    v8::Local<v8::Value> receiver = self->slot->get(isolate);
    if (receiver.IsEmpty())
    {
        raise(PyExc_RuntimeError, "this handle was released when its realm closed");
        return v8::Local<v8::Object>();
    }
    v8::Local<v8::Object> object;
    if (!receiver->ToObject(context).ToLocal(&object))
        raise(PyExc_TypeError, "a JavaScript value cannot take properties");
    return object;
}

// ``delete value[name]``.  The answer is whether JavaScript deleted it, which
// is false for a non-configurable property -- an exception only when the
// deletion itself failed.
PyObject* handle_delete(ValueObject* self, PyObject* name)
{
    Realm* realm = usable_realm(self, "delete()");
    if (realm == nullptr)
        return nullptr;
    if (!PyUnicode_Check(name))
        return raise(PyExc_TypeError, "a JavaScript property name must be a string");

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> object = receiver_object(self, isolate, context);
    if (object.IsEmpty())
        return nullptr;
    v8::Local<v8::Value> key = to_js(name, *realm);
    if (key.IsEmpty())
        return nullptr;

    v8::TryCatch try_catch(isolate);
    v8::Maybe<bool> deleted = object->Delete(context, key);
    if (deleted.IsNothing())
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to delete a JavaScript property");
        return nullptr;
    }
    return PyBool_FromLong(deleted.FromJust() ? 1 : 0);
}

// The value's own enumerable string keys, as a tuple.
//
// This is `Object.keys`, not materialisation: it reads no property values, so
// asking a host object what it has cannot run any of the getters it answers
// with.
PyObject* handle_keys(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "keys()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> object = receiver_object(self, isolate, context);
    if (object.IsEmpty())
        return nullptr;

    v8::Local<v8::Array> names;
    if (!object->GetOwnPropertyNames(
             context,
             static_cast<v8::PropertyFilter>(v8::ONLY_ENUMERABLE | v8::SKIP_SYMBOLS),
             v8::KeyConversionMode::kConvertToString)
             .ToLocal(&names))
        return raise(PyExc_RuntimeError, "failed to enumerate a JavaScript object");

    const uint32_t count = names->Length();
    PyObject* result = PyTuple_New(static_cast<Py_ssize_t>(count));
    if (result == nullptr)
        return nullptr;

    for (uint32_t index = 0; index < count; ++index)
    {
        v8::Local<v8::Value> name;
        if (!names->Get(context, index).ToLocal(&name))
        {
            Py_DECREF(result);
            return raise(PyExc_RuntimeError, "failed to read an object key");
        }
        v8::String::Utf8Value utf8(isolate, name);
        if (*utf8 == nullptr)
        {
            Py_DECREF(result);
            return raise(PyExc_UnicodeDecodeError, "a JavaScript key was not valid UTF-8");
        }
        // PyTuple_SetItem steals the reference.
        PyObject* text = PyUnicode_DecodeUTF8(*utf8, utf8.length(), "replace");
        if (text == nullptr)
        {
            Py_DECREF(result);
            return nullptr;
        }
        PyTuple_SetItem(result, static_cast<Py_ssize_t>(index), text);
    }
    return result;
}

// The value's ``[Symbol.iterator]`` method, or ``undefined`` when it has none.
//
// A protocol member rather than a named property, so it cannot be reached by
// name from Python and has to be asked for here.  The caller invokes what it
// gets with ``call(value)``, which is why the receiver travels with the answer
// rather than with the method.
PyObject* handle_iterator(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "iterator()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Object> object = receiver_object(self, isolate, context);
    if (object.IsEmpty())
        return nullptr;

    v8::TryCatch try_catch(isolate);
    v8::Local<v8::Value> method;
    if (!object->Get(context, v8::Symbol::GetIterator(isolate)).ToLocal(&method))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to read a JavaScript iterator");
        return nullptr;
    }
    return to_python_boundary(method, *realm);
}

// The conversion operations JavaScript defines on a value, one method each.
//
// They are asked for explicitly rather than applied on the way out, which is
// what keeps the boundary three-state: a host that wants `String(value)` says
// so, and a host that merely holds the value gets the handle back.
PyObject* handle_to_string(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "to_string()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::TryCatch try_catch(isolate);
    v8::Local<v8::String> text;
    if (!self->slot->get(isolate)->ToString(context).ToLocal(&text))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to convert a JavaScript value to a string");
        return nullptr;
    }

    v8::String::Utf8Value utf8(isolate, text);
    if (*utf8 == nullptr)
        return raise(PyExc_UnicodeDecodeError, "the JavaScript string was not valid UTF-8");
    return PyUnicode_DecodeUTF8(*utf8, utf8.length(), "replace");
}

PyObject* handle_to_number(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "to_number()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::TryCatch try_catch(isolate);
    v8::Local<v8::Number> number;
    if (!self->slot->get(isolate)->ToNumber(context).ToLocal(&number))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to convert a JavaScript value to a number");
        return nullptr;
    }
    return PyFloat_FromDouble(number->Value());
}

PyObject* handle_to_boolean(ValueObject* self, PyObject*)
{
    Realm* realm = usable_realm(self, "to_boolean()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    // Boolean(value) never fails, so there is nothing to catch.
    const bool truthy = self->slot->get(isolate)->ToBoolean(isolate)->IsTrue();
    return PyBool_FromLong(truthy ? 1 : 0);
}

// ``call(receiver, *args)``: the same call as ``value(*args)``, with the
// receiver named instead of undefined.
//
// The iterator protocol needs it -- ``next`` must be called on the iterator it
// came from -- and a host object that stands for a JavaScript method has no
// other way to say which object it is a method of.
PyObject* handle_call_with_receiver(ValueObject* self, PyObject* args, PyObject* kwargs)
{
    if (kwargs != nullptr && PyDict_Size(kwargs) != 0)
        return raise(PyExc_TypeError, "a JavaScript function takes positional arguments only");

    const Py_ssize_t count = PyTuple_Size(args);
    if (count < 1)
        return raise(PyExc_TypeError, "call() needs the receiver as its first argument");
    PyObject* receiver = PyTuple_GetItem(args, 0);
    if (receiver == nullptr)
        return nullptr;

    Realm* realm = usable_realm(self, "call()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Value> this_value;
    if (receiver == Py_None)
        this_value = v8::Undefined(isolate);
    else
        this_value = to_js(receiver, *realm);
    if (this_value.IsEmpty())
        return nullptr;

    return invoke(self, *realm, isolate, context, this_value, args, 1);
}

int handle_setattro(ValueObject* self, PyObject* name, PyObject* value)
{
    Realm* realm = usable_realm(self, "attribute assignment");
    if (realm == nullptr)
        return -1;
    if (!PyUnicode_Check(name))
    {
        raise(PyExc_TypeError, "a JavaScript property name must be a string");
        return -1;
    }

    PyEntry entry(*realm);
    if (!entry.ok())
        return -1;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    v8::Local<v8::Value> key = to_js(name, *realm);
    if (key.IsEmpty())
        return -1;
    v8::Local<v8::Value> converted = to_js(value, *realm);
    if (converted.IsEmpty())
        return -1;

    v8::Local<v8::Value> receiver = self->slot->get(isolate);
    v8::Local<v8::Object> object;
    if (!receiver->ToObject(context).ToLocal(&object))
    {
        raise(PyExc_TypeError, "a JavaScript value cannot take properties");
        return -1;
    }

    v8::TryCatch try_catch(isolate);
    if (!object->Set(context, key, converted).FromMaybe(false))
    {
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *realm);
        else
            raise(PyExc_RuntimeError, "failed to set a JavaScript property");
        return -1;
    }
    return 0;
}

PyObject* handle_call(ValueObject* self, PyObject* args, PyObject* kwargs)
{
    if (kwargs != nullptr && PyDict_Size(kwargs) != 0)
        return raise(PyExc_TypeError, "a JavaScript function takes positional arguments only");

    Realm* realm = usable_realm(self, "call()");
    if (realm == nullptr)
        return nullptr;

    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    return invoke(self, *realm, isolate, context, v8::Undefined(isolate), args, 0);
}

PyObject* handle_get_valid(ValueObject* self, void*)
{
    const bool valid = self->slot != nullptr && self->realm != nullptr && self->realm->is_active();
    return PyBool_FromLong(valid ? 1 : 0);
}

PyObject* handle_repr(ValueObject* self)
{
    if (self->slot == nullptr || self->realm == nullptr)
        return PyUnicode_FromString("<v8n.Value released>");

    PyEntry entry(*self->realm);
    if (!entry.ok())
    {
        PyErr_Clear();
        return PyUnicode_FromString("<v8n.Value released>");
    }
    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Value> value = self->slot->get(isolate);
    v8::String::Utf8Value text(isolate, value);
    if (*text == nullptr)
        return PyUnicode_FromString("<v8n.Value>");
    return PyUnicode_FromFormat("<v8n.Value %s>", *text);
}

PyMethodDef handle_methods[] = {
    {"to_python", reinterpret_cast<PyCFunction>(handle_to_python), METH_NOARGS,
     "to_python() -> object\n\n"
     "Materialise arrays and plain objects recursively, preserving cycles and "
     "shared references.  Everything else stays a handle."},
    {"get", reinterpret_cast<PyCFunction>(handle_get), METH_O,
     "get(name) -> object\n\n"
     "Read one JavaScript property by name.  Use this rather than ``getattr`` "
     "for an arbitrary name, because a Python attribute of the wrapper wins "
     "there and a JavaScript property named ``to_python`` would be unreachable."},
    {"delete", reinterpret_cast<PyCFunction>(handle_delete), METH_O,
     "delete(name) -> bool\n\n"
     "Delete one JavaScript property, answering whether JavaScript deleted it. "
     "False means the property is non-configurable, which is not an error."},
    {"keys", reinterpret_cast<PyCFunction>(handle_keys), METH_NOARGS,
     "keys() -> tuple[str, ...]\n\n"
     "The value's own enumerable string keys, as ``Object.keys``.  Reading the "
     "keys reads no property values, so it cannot run the getters the object "
     "answers with."},
    {"iterator", reinterpret_cast<PyCFunction>(handle_iterator), METH_NOARGS,
     "iterator() -> object\n\n"
     "The value's ``[Symbol.iterator]`` method, or ``undefined`` when it has "
     "none.  Call it with ``call(value)`` to get the iterator."},
    {"call", reinterpret_cast<PyCFunction>(handle_call_with_receiver),
     METH_VARARGS | METH_KEYWORDS,
     "call(receiver, *args) -> object\n\n"
     "Call the value with an explicit receiver.  ``None`` is the JavaScript "
     "``undefined``; ``value(*args)`` is the same call with that receiver."},
    {"to_string", reinterpret_cast<PyCFunction>(handle_to_string), METH_NOARGS,
     "to_string() -> str\n\n"
     "``String(value)``: the conversion JavaScript defines, not a Python "
     "rendering of the handle."},
    {"to_number", reinterpret_cast<PyCFunction>(handle_to_number), METH_NOARGS,
     "to_number() -> float\n\n"
     "``Number(value)``: the conversion JavaScript defines.  A value that is "
     "not numeric answers NaN rather than raising."},
    {"to_boolean", reinterpret_cast<PyCFunction>(handle_to_boolean), METH_NOARGS,
     "to_boolean() -> bool\n\n"
     "``Boolean(value)``: the conversion JavaScript defines, which is why an "
     "empty object is true and an empty string is not."},
    {nullptr, nullptr, 0, nullptr},
};

PyObject* handle_get_is_array(ValueObject* self, void*)
{
    Realm* realm = usable_realm(self, "is_array");
    if (realm == nullptr)
        return nullptr;

    // Reading the value needs a handle scope, like every other read of it.
    PyEntry entry(*realm);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = entry.context();
    v8::Context::Scope context_scope(context);

    return PyBool_FromLong(self->slot->get(isolate)->IsArray() ? 1 : 0);
}

PyGetSetDef handle_getset[] = {
    {const_cast<char*>("is_valid"), reinterpret_cast<getter>(handle_get_valid), nullptr,
     const_cast<char*>("Whether the realm that produced this handle is still open."), nullptr},
    {const_cast<char*>("is_array"), reinterpret_cast<getter>(handle_get_is_array), nullptr,
     const_cast<char*>("Whether the value is a JavaScript array."), nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

PyType_Slot handle_slots[] = {
    {Py_tp_dealloc, reinterpret_cast<void*>(handle_dealloc)},
    {Py_tp_traverse, reinterpret_cast<void*>(handle_traverse)},
    {Py_tp_clear, reinterpret_cast<void*>(handle_clear)},
    {Py_tp_getattro, reinterpret_cast<void*>(handle_getattro)},
    {Py_tp_setattro, reinterpret_cast<void*>(handle_setattro)},
    {Py_tp_call, reinterpret_cast<void*>(handle_call)},
    {Py_tp_methods, handle_methods},
    {Py_tp_getset, handle_getset},
    {Py_tp_repr, reinterpret_cast<void*>(handle_repr)},
    {Py_tp_doc, const_cast<char*>("A retained JavaScript value, valid while its realm is open.")},
    {0, nullptr},
};

PyType_Spec handle_spec = {
    "v8n.Value",
    sizeof(ValueObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    handle_slots,
};

}  // namespace

int
register_handle_type(PyObject* module)
{
    value_type = reinterpret_cast<PyTypeObject*>(PyType_FromSpec(&handle_spec));
    if (value_type == nullptr)
        return -1;
    if (PyModule_AddObjectRef(module, "Value", reinterpret_cast<PyObject*>(value_type)) < 0)
        return -1;
    return 0;
}

}  // namespace v8n::value

namespace v8n
{

PyObject*
value_wrapper(Realm& realm, v8::Local<v8::Value> value)
{
    if (value.IsEmpty())
        return raise(PyExc_RuntimeError, "cannot wrap an empty handle");

    auto* self = reinterpret_cast<ValueObject*>(
        PyType_GenericAlloc(value::value_type, 0));
    if (self == nullptr)
        return nullptr;

    self->slot = new (std::nothrow) HandleSlot<v8::Value>();
    if (self->slot == nullptr)
    {
        Py_DECREF(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->realm = &realm;
    if (!self->slot->emplace(realm.isolate(), value))
    {
        Py_DECREF(reinterpret_cast<PyObject*>(self));
        return raise(PyExc_RuntimeError, "failed to retain the JavaScript value") != nullptr
                   ? nullptr
                   : nullptr;
    }

    PyObject* realm_object = realm.python_object();
    self->realm_object = realm_object;
    Py_XINCREF(self->realm_object);
    return reinterpret_cast<PyObject*>(self);
}

int
register_value_types(PyObject* module)
{
    if (value::register_primitives(module) < 0)
        return -1;
    if (value::register_handle_type(module) < 0)
        return -1;
    return 0;
}

}  // namespace v8n
