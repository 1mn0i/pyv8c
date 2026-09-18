// The V8 callbacks a batch installs.
//
// Every one of them recovers its MemberBinding from the data V8 hands back, so
// nothing here looks a member up by name.  That is the whole reason the batch
// carries a tuple of callables and indexes into it: a dispatch is a pointer
// dereference, and the hot path allocates nothing but the argument tuple Python
// needs anyway.
//
// The receiver is found through the same ownership machinery as every other
// crossing, so a method called on a host object reaches that object's Python
// instance without a second, parallel way of recording identity.

#include "bridge/bridge_internal.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/v8_compat.h"
#include "value/value_internal.h"
#include "v8n/boundary.h"
#include "v8n/realm.h"
#include "v8n/value.h"

#include <cstring>

namespace v8n::bridge
{
namespace
{

MemberBinding*
binding_of(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        return nullptr;
    return static_cast<MemberBinding*>(v8c::external_value(info.Data()));
}

// The callbacks V8 only offers as property callbacks: a lazy data property and a
// static native data property.  None of them needs a receiver, so none of them
// misses This().
MemberBinding*
binding_of(const v8::PropertyCallbackInfo<v8::Value>& info)
{
    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        return nullptr;
    return static_cast<MemberBinding*>(v8c::external_value(info.Data()));
}

MemberBinding*
binding_of(const v8::PropertyCallbackInfo<void>& info)
{
    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        return nullptr;
    return static_cast<MemberBinding*>(v8c::external_value(info.Data()));
}



// A member's binding and realm, or null with a JavaScript exception thrown.
// Every callback starts here, so no path can reach Python with a stale binding.
MemberBinding*
context_of(v8::Isolate* isolate, Realm** realm, MemberBinding* binding)
{
    *realm = Realm::from_context(isolate->GetCurrentContext());
    if (*realm == nullptr || binding == nullptr)
        return nullptr;

    // A realm that is closing refuses new work.  Its callbacks are still
    // reachable from JavaScript for as long as the context lives, so they have to
    // check rather than assume.
    if (!(*realm)->is_active())
    {
        isolate->ThrowException(v8::Exception::TypeError(v8::String::NewFromUtf8Literal(
            isolate, "this host object belongs to a realm that is closing")));
        return nullptr;
    }
    return binding;
}

// The common refusal for a callback whose data does not describe a realm.  It
// cannot happen through a batch, which is exactly why it says so rather than
// returning undefined: a silent undefined here would look like a property that
// does not exist.
void
refuse(v8::Isolate* isolate, const char* what)
{
    // NewFromUtf8, not NewFromUtf8Literal: the literal form needs a literal, and
    // these messages are chosen at run time.
    v8::Local<v8::String> message;
    if (!v8::String::NewFromUtf8(isolate, what, v8::NewStringType::kNormal).ToLocal(&message))
        return;
    isolate->ThrowException(v8::Exception::TypeError(message));
}

// Calls a Python callable with one argument, translating the result.  Used by
// both accessors, which differ only in whether a value is written back.
v8::Local<v8::Value>
call_with_receiver(v8::Isolate* isolate, Realm& realm, PyObject* callable, PyObject* receiver,
                   PyObject* second, bool* failed)
{
    *failed = false;
    PyRef result(PyRef::adopt(second == nullptr
                                  ? PyObject_CallFunctionObjArgs(callable, receiver, nullptr)
                                  : PyObject_CallFunctionObjArgs(callable, receiver, second,
                                                                 nullptr)));
    if (!result)
    {
        *failed = true;
        throw_python_error(isolate);
        return v8::Local<v8::Value>();
    }

    v8::Local<v8::Value> converted = to_js(result.get(), realm);
    if (converted.IsEmpty())
    {
        *failed = true;
        throw_python_error(isolate);
    }
    return converted;
}

}  // namespace

PyObject*
receiver_of(v8::Local<v8::Object> holder, v8::Isolate* isolate)
{
    if (holder.IsEmpty())
        return nullptr;

    Realm* realm = Realm::from_context(isolate->GetCurrentContext());
    if (realm == nullptr)
        return nullptr;

    PythonOwner* record = nullptr;
    if (!python_owner_of(holder, *realm, &record))
        return nullptr;
    return record->instance;
}

void
trace_event(Realm& realm, TraceEvent event, const char* member)
{
    if (!realm.is_tracing())
        return;
    realm.trace(event, member, "");
}

void
attribute_getter(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
    {
        refuse(isolate, "this host property has no binding");
        return;
    }

    // This(), which is the object the property was read through.  It is where the
    // Python identity lives, because that is the object construction adopted.
    PyObject* receiver = receiver_of(info.This(), isolate);
    if (receiver == nullptr)
    {
        refuse(isolate, "this host property was read from an object that is not a host object");
        return;
    }

    PyCallback guard("a JavaScript read of a host attribute");
    trace_event(*realm, TraceEvent::kGet, binding->member_name);

    bool failed = false;
    v8::Local<v8::Value> converted =
        call_with_receiver(isolate, *realm, binding->callable, receiver, nullptr, &failed);
    if (failed)
        return;

    info.GetReturnValue().Set(converted);
}

void
attribute_setter(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
        return;
    if (binding->setter == nullptr)
    {
        // A read-only attribute reached its setter anyway, which means a flag and
        // a slot disagree.  Saying so beats silently dropping the write.
        refuse(isolate, "this host attribute is read-only");
        return;
    }

    PyObject* receiver = receiver_of(info.This(), isolate);
    if (receiver == nullptr)
    {
        refuse(isolate, "this host property was written on an object that is not a host object");
        return;
    }

    PyCallback guard("a JavaScript write to a host attribute");
    trace_event(*realm, TraceEvent::kSet, binding->member_name);

    const int count = info.Length();
    if (count < 1)
    {
        refuse(isolate, "a host attribute was written without a value");
        return;
    }

    PyRef converted(PyRef::adopt(to_python_boundary(info[0], *realm)));
    if (!converted)
    {
        throw_python_error(isolate);
        return;
    }

    bool failed = false;
    call_with_receiver(isolate, *realm, binding->setter, receiver, converted.get(), &failed);
    if (failed)
        return;

    // A replaceable attribute becomes a data property on this object after the
    // first write, so the value is read back with no accessor in the way.  Only at
    // the instance location: on the prototype it would replace the attribute for
    // every instance at once, which is the opposite of what replaceable means.
    const MemberSpec* spec = binding->spec;
    if (spec == nullptr || (spec->flags & kMemberReplaceable) == 0 ||
        spec->location != kLocationInstance)
        return;

    v8::Local<v8::String> property_name;
    if (!v8::String::NewFromUtf8(isolate, spec->name, v8::NewStringType::kNormal)
             .ToLocal(&property_name))
        return;

    v8::Local<v8::Value> written = to_js(converted.get(), *realm);
    if (written.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    if (!info.This()->CreateDataProperty(isolate->GetCurrentContext(), property_name, written)
             .FromMaybe(false))
        throw_python_error(isolate);
}

void
constant_getter(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
    {
        refuse(isolate, "this host constant has no binding");
        return;
    }

    PyCallback guard("a JavaScript read of a host constant");
    trace_event(*realm, TraceEvent::kGet, binding->member_name);

    // No receiver: a constant is produced for whoever asks, and asking for one is
    // not an operation on an instance.
    PyRef result(PyRef::adopt(PyObject_CallObject(binding->callable, nullptr)));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }

    v8::Local<v8::Value> converted = to_js(result.get(), *realm);
    if (converted.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    info.GetReturnValue().Set(converted);
}

void
lazy_constant_getter(v8::Local<v8::Name> name,
                     const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
    {
        refuse(isolate, "this host constant has no binding");
        return;
    }

    PyCallback guard("a JavaScript read of a host constant");
    trace_event(*realm, TraceEvent::kGet, binding->member_name);

    PyRef result(PyRef::adopt(PyObject_CallObject(binding->callable, nullptr)));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }

    v8::Local<v8::Value> converted = to_js(result.get(), *realm);
    if (converted.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    info.GetReturnValue().Set(converted);
}

void
static_attribute_getter(v8::Local<v8::Name> name,
                        const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
    {
        refuse(isolate, "this host attribute has no binding");
        return;
    }

    PyCallback guard("a JavaScript read of a host static attribute");
    trace_event(*realm, TraceEvent::kGet, binding->member_name);

    PyRef result(PyRef::adopt(PyObject_CallObject(binding->callable, nullptr)));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }

    v8::Local<v8::Value> converted = to_js(result.get(), *realm);
    if (converted.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    info.GetReturnValue().Set(converted);
}

void
static_attribute_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value,
                        const v8::PropertyCallbackInfo<void>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
        return;
    if (binding->setter == nullptr)
    {
        refuse(isolate, "this host attribute is read-only");
        return;
    }

    PyCallback guard("a JavaScript write to a host static attribute");
    trace_event(*realm, TraceEvent::kSet, binding->member_name);

    PyRef converted(PyRef::adopt(to_python_boundary(value, *realm)));
    if (!converted)
    {
        throw_python_error(isolate);
        return;
    }

    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(binding->setter, converted.get(),
                                                           nullptr)));
    if (!result)
        throw_python_error(isolate);
}

namespace
{

// The body shared by a method call and an instance call.
//
// The only thing that differs is whether the receiver is passed to Python: an
// interface member is static and has no instance to pass, while a host object
// invoked as a function is its own receiver.
void
dispatch_host_call(const v8::FunctionCallbackInfo<v8::Value>& info, bool wants_receiver)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr)
        return;

    PyObject* receiver = nullptr;
    if (wants_receiver)
    {
        // This(), not Holder(): a call has a receiver, not a holder.  For a host
        // method the two are the same object, and This() is the one a call
        // actually has.
        receiver = receiver_of(info.This(), isolate);
        if (receiver == nullptr)
        {
            isolate->ThrowException(v8::Exception::TypeError(v8::String::NewFromUtf8Literal(
                isolate, "this host method was called on an object that is not a host object")));
            return;
        }
    }

    PyCallback guard("a JavaScript call into a host method");
    trace_event(*realm, TraceEvent::kCall, binding->member_name);

    const int count = info.Length();
    PyRef arguments(PyRef::adopt(PyTuple_New(static_cast<Py_ssize_t>(count) +
                                             (wants_receiver ? 1 : 0))));
    if (!arguments)
    {
        throw_python_error(isolate);
        return;
    }

    // PyTuple_SetItem rather than the macro: the limited API has no
    // PyTuple_SET_ITEM, and a fresh tuple makes the difference a call rather than
    // a copy.
    Py_ssize_t at = 0;
    if (wants_receiver)
    {
        // The tuple takes its own reference from here on, and releases it with
        // itself.
        if (PyTuple_SetItem(arguments.get(), at++, PyRef::borrow(receiver).release()) < 0)
        {
            throw_python_error(isolate);
            return;
        }
    }
    for (int index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(to_python_boundary(info[index], *realm)));
        if (!item)
        {
            throw_python_error(isolate);
            return;
        }
        if (PyTuple_SetItem(arguments.get(), at++, item.release()) < 0)
        {
            throw_python_error(isolate);
            return;
        }
    }

    PyRef result(PyRef::adopt(PyObject_CallObject(binding->callable, arguments.get())));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }

    v8::Local<v8::Value> converted = to_js(result.get(), *realm);
    if (converted.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    info.GetReturnValue().Set(converted);
}

}  // namespace

void
method_call(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    // An interface member is static: there is no instance to pass, and asking for
    // one would fail because the receiver is the constructor function.
    const MemberBinding* binding = binding_of(info);
    dispatch_host_call(info, binding != nullptr && binding->spec != nullptr &&
                                binding->spec->location != kLocationInterface);
}

void
instance_call(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    // No location and no receiver check apply, because there is exactly one
    // receiver and it is the object that was called.  A check would only be able
    // to fail, which is not a state a call can be in.
    dispatch_host_call(info, true);
}

void
interface_construct(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = context_of(isolate, &realm, binding_of(info));
    if (binding == nullptr || binding->interface_spec == nullptr)
        return;

    const InterfaceSpec& spec = *binding->interface_spec;
    const bool is_construct = info.IsConstructCall();
    const bool can_new = (spec.constructor_behavior & kConstructNew) != 0;
    const bool can_call = (spec.constructor_behavior & kConstructCall) != 0;

    v8::Local<v8::String> class_name;
    v8::String::NewFromUtf8(isolate, spec.name, v8::NewStringType::kNormal)
        .ToLocal(&class_name);

    // The four refusals are separate because JavaScript distinguishes them, and
    // an interface that is a namespace reads very differently from one that is a
    // constructor used without `new`.
    if (!can_new && !can_call)
    {
        isolate->ThrowException(
            v8::Exception::TypeError(v8::String::NewFromUtf8Literal(isolate, "Illegal constructor")));
        return;
    }
    if (is_construct && !can_new)
    {
        isolate->ThrowException(v8::Exception::TypeError(v8::String::Concat(
            isolate, class_name, v8::String::NewFromUtf8Literal(isolate, " is not a constructor"))));
        return;
    }
    if (!is_construct && !can_call)
    {
        isolate->ThrowException(v8::Exception::TypeError(v8::String::Concat(
            isolate, class_name,
            v8::String::NewFromUtf8Literal(
                isolate, " cannot be called without the 'new' operator"))));
        return;
    }
    if ((spec.traits & kTraitUnconstructable) != 0)
    {
        isolate->ThrowException(v8::Exception::TypeError(v8::String::Concat(
            isolate, class_name, v8::String::NewFromUtf8Literal(isolate, " is not a constructor"))));
        return;
    }
    if (binding->callable == nullptr)
    {
        // The description says this interface can be constructed and gives it no
        // adapter, so there is nothing to build the Python side from.
        isolate->ThrowException(v8::Exception::TypeError(v8::String::Concat(
            isolate, class_name,
            v8::String::NewFromUtf8Literal(
                isolate, " cannot be constructed: no adapter was provided"))));
        return;
    }

    PyCallback guard("a JavaScript construction of a host interface");
    trace_event(*realm, TraceEvent::kConstruct, spec.name);

    const int count = info.Length();
    PyRef arguments(PyRef::adopt(PyTuple_New(static_cast<Py_ssize_t>(count))));
    PyRef keywords(PyRef::adopt(PyDict_New()));
    if (!arguments || !keywords)
    {
        throw_python_error(isolate);
        return;
    }
    for (int index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(to_python_boundary(info[index], *realm)));
        if (!item)
        {
            throw_python_error(isolate);
            return;
        }
        if (PyTuple_SetItem(arguments.get(), index, item.release()) < 0)
        {
            throw_python_error(isolate);
            return;
        }
    }

    // The adapter takes (arguments, keywords) and returns the instance to back
    // the new object.  It is how a host decides what an interface's construction
    // means without this layer knowing anything about its classes.
    PyRef instance(PyRef::adopt(PyObject_CallFunctionObjArgs(binding->callable, arguments.get(),
                                                            keywords.get(), nullptr)));
    if (!instance)
    {
        throw_python_error(isolate);
        return;
    }

    // V8 already made the object from the interface's template, so it carries the
    // prototype chain; giving it the Python identity is what makes `instanceof`
    // and property access both work on the same object.
    if (is_construct)
    {
        v8::Local<v8::Object> holder = info.This();
        if (!adopt_python_object(*realm, holder, instance.get()))
        {
            throw_python_error(isolate);
            return;
        }
        info.GetReturnValue().Set(holder);
        return;
    }

    // Called without `new`: the adapter's instance still has to be handed back,
    // wrapped, because there is no object to adopt into.
    v8::Local<v8::Value> wrapped = wrap_python_object(*realm, instance.get());
    if (wrapped.IsEmpty())
    {
        throw_python_error(isolate);
        return;
    }
    info.GetReturnValue().Set(wrapped);
}

// -- interceptors ---------------------------------------------------------

namespace
{

HandlerData*
handler_data_of(v8::Local<v8::Value> data)
{
    if (data.IsEmpty() || !data->IsExternal())
        return nullptr;
    return static_cast<HandlerData*>(v8c::external_value(data));
}

// The property name, as Python sees it.  A name becomes a string and an index
// becomes an int, because that is what the host declared: `handlers` are keyed by
// whatever JavaScript looked up.
PyObject*
name_key(v8::Local<v8::Name> name, v8::Local<v8::Context> context)
{
    v8::Local<v8::String> text;
    if (!name->ToString(context).ToLocal(&text))
        return nullptr;
    v8::String::Utf8Value utf8(v8c::context_isolate(context), text);
    if (*utf8 == nullptr)
        return nullptr;
    return PyUnicode_DecodeUTF8(*utf8, utf8.length(), "replace");
}

PyObject*
index_key(uint32_t index)
{
    return PyLong_FromUnsignedLong(index);
}

// Calls a handler with the receiver, the key, and the value for a write.
//
// The receiver comes first and is not optional.  A handler is one function per
// interface rather than per object, so without it a named getter cannot know which
// object is being asked -- and it is the object, not the interface, that owns the
// answer: a window has as many properties as the page puts there.
PyObject*
call_handler(v8::Isolate* isolate, PyObject* callable, PyObject* receiver, PyObject* key,
             PyObject* second)
{
    PyObject* result =
        second == nullptr
            ? PyObject_CallFunctionObjArgs(callable, receiver, key, nullptr)
            : PyObject_CallFunctionObjArgs(callable, receiver, key, second, nullptr);
    if (result == nullptr)
        throw_python_error(isolate);
    return result;
}

// Opens an interceptor: which realm, which handler, and which object.
//
// A handler configuration carries one data for all of its callbacks, so the
// bindings arrive as a table and each callback picks the role it implements --
// which it knows, because a callback only ever implements one.
//
// One function per interface answers for every object of that interface, so the
// handler also has to be told which object is asking.  The interceptor is installed
// on the instance template and Holder() is the object it was found on, so it is the
// instance -- and a receiver that is not a host object of this realm means the
// access came in somewhere with no Python instance behind it, which is what
// returning false says.
template <typename CallbackInfo>
bool
open_interceptor(const CallbackInfo& info, HandlerRole role, Realm** realm,
                 MemberBinding** binding, PyObject** receiver)
{
    v8::Isolate* isolate = info.GetIsolate();
    HandlerData* data = handler_data_of(info.Data());
    *realm = data == nullptr ? nullptr : data->realm;
    *binding = data == nullptr ? nullptr : data->roles[role];
    *receiver = nullptr;
    if (*realm == nullptr || *binding == nullptr || (*binding)->callable == nullptr)
        return false;

    if (!(*realm)->is_active())
    {
        refuse(isolate, "this host object belongs to a realm that is closing");
        return false;
    }

    // Holder(), not This(): a property callback has no This().  The interceptor is
    // installed on the instance template, so the object it is found on is the
    // instance -- which is exactly the receiver an access has.  An accessor cannot
    // say the same, which is why attributes are function templates instead; see
    // bridge_internal.h.
    *receiver = receiver_of(info.Holder(), isolate);
    return *receiver != nullptr;
}

// A trace hook that cannot see which property was touched is not worth
// installing, so an intercepted event names the key rather than the handler.
void
trace_key(Realm& realm, TraceEvent event, PyObject* key)
{
    if (!realm.is_tracing())
        return;
    const char* text = abi3::Utf8(key);
    if (text == nullptr)
    {
        PyErr_Clear();
        return;
    }
    realm.trace(event, text, "");
}

// The answer to an intercepted read or write: a converted value, or a report that
// the host declined.
enum class Answer
{
    kDeclined,
    kAnswered,
    kFailed,
};

Answer
intercepted_value(v8::Isolate* isolate, Realm& realm, PyObject* callable, PyObject* receiver,
                  PyObject* key, v8::Local<v8::Value>* out)
{
    PyRef result(PyRef::adopt(call_handler(isolate, callable, receiver, key, nullptr)));
    if (!result)
        return Answer::kFailed;
    if (result.get() == Py_None)
        return Answer::kDeclined;

    *out = to_js(result.get(), realm);
    if (out->IsEmpty())
    {
        throw_python_error(isolate);
        return Answer::kFailed;
    }
    return Answer::kAnswered;
}

// A query answers with the property's attribute mask, or says it has no opinion.
//
// A mask rather than a bool, because that is what V8 asks for: kYes means the
// property exists and the return value is its attributes.  A host that needs `in`
// to be true for a read-only name has to be able to say so, and a bool cannot:
// it can only ever mean "present, with no attributes".
//
//   None   -> this handler has no opinion, and the lookup continues
//   bool   -> True: present with no attributes.  False: no opinion, because
//             "absent" and "present with no attributes" are different answers and
//             `False` reads as the first one.
//   int    -> present, with that attribute mask
//
// Anything else is "no opinion" too, which is what keeps a handler from having to
// know every name in existence in order to stay out of the way.
Answer
intercepted_attributes(v8::Isolate* isolate, PyObject* callable, PyObject* receiver, PyObject* key,
                       int* out)
{
    PyRef result(PyRef::adopt(call_handler(isolate, callable, receiver, key, nullptr)));
    if (!result)
        return Answer::kFailed;
    if (result.get() == Py_None)
        return Answer::kDeclined;
    if (PyBool_Check(result.get()))
    {
        if (result.get() == Py_False)
            return Answer::kDeclined;
        *out = 0;
        return Answer::kAnswered;
    }

    const long mask = PyLong_AsLong(result.get());
    if (mask == -1 && PyErr_Occurred())
    {
        // Not an integer: some other absent-answer convention the host uses.
        PyErr_Clear();
        return Answer::kDeclined;
    }
    if (mask < 0 || mask > 7)
        return Answer::kDeclined;
    *out = static_cast<int>(mask);
    return Answer::kAnswered;
}

// A delete answers with a bool and nothing else, because "did you delete it" is
// the whole question.  There is no mask to report and no third answer.
Answer
intercepted_flag(v8::Isolate* isolate, PyObject* callable, PyObject* receiver, PyObject* key,
                 bool* out)
{
    (void)isolate;
    // Called directly rather than through call_handler, because a failed delete
    // is the one failure the caller may have to answer silently and converting
    // the exception here would leave it nothing left to inspect.
    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(callable, receiver, key, nullptr)));
    if (!result)
        return Answer::kFailed;
    if (!PyBool_Check(result.get()))
        return Answer::kDeclined;
    *out = result.get() == Py_True;
    return Answer::kAnswered;
}

// A host may raise to say that a write or a delete *failed* rather than that
// something went wrong: `__v8_interceptor_failure__` on the exception's type is
// that declaration, and it is what a legacy read-only property does.  V8's
// contract for an interceptor that handled an operation it could not perform is
// `GetReturnValue().Set(false)`, which is a TypeError in strict mode and silence
// outside it -- the behaviour a page expects from `list[0] = 1` on a NodeList.
//
// So the exception is thrown when the caller asked for strict semantics, and
// swallowed otherwise.  Returns true when the operation was declared failed and
// the caller should answer with false.
bool
settle_write_failure(v8::Isolate* isolate, bool throw_on_error)
{
    PyObject* type = nullptr;
    PyObject* value = nullptr;
    PyObject* traceback = nullptr;
    PyErr_Fetch(&type, &value, &traceback);

    bool declared = false;
    if (type != nullptr)
    {
        // On the type rather than the instance, because that is where a class
        // attribute lives and the host declares it once per error class.
        PyObject* flag = PyObject_GetAttrString(type, "__v8_interceptor_failure__");
        if (flag != nullptr)
        {
            declared = PyObject_IsTrue(flag) > 0;
            Py_DECREF(flag);
        }
        else
        {
            PyErr_Clear();
        }
    }

    PyErr_Restore(type, value, traceback);
    if (!declared || throw_on_error)
    {
        throw_python_error(isolate);
        return false;
    }
    PyErr_Clear();
    return true;
}

}  // namespace

v8::Intercepted
named_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerNameGet, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript read of an intercepted property");
    PyRef key(PyRef::adopt(name_key(name, isolate->GetCurrentContext())));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kGet, key.get());

    v8::Local<v8::Value> converted;
    switch (intercepted_value(isolate, *realm, binding->callable, receiver, key.get(), &converted))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }
    info.GetReturnValue().Set(converted);
    return v8::Intercepted::kYes;
}

v8::Intercepted
named_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value,
             const v8::PropertyCallbackInfo<void>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerNameSet, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript write to an intercepted property");
    PyRef key(PyRef::adopt(name_key(name, isolate->GetCurrentContext())));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kSet, key.get());

    PyRef converted(PyRef::adopt(to_python_boundary(value, *realm)));
    if (!converted)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    // Called directly rather than through call_handler: a failed write may be an
    // operation the host declared failed rather than an error to report, and
    // converting the exception here would leave nothing to inspect.
    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(
        binding->callable, receiver, key.get(), converted.get(), nullptr)));
    if (!result)
    {
        if (settle_write_failure(isolate, info.ShouldThrowOnError()))
            info.GetReturnValue().Set(false);
        return v8::Intercepted::kYes;
    }
    return result.get() == Py_None ? v8::Intercepted::kNo : v8::Intercepted::kYes;
}

v8::Intercepted
named_query(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerNameQuery, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript existence test on an intercepted object");
    PyRef key(PyRef::adopt(name_key(name, isolate->GetCurrentContext())));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kHas, key.get());

    // The V2 contract, which is not the V1 one: kYes means the property exists and
    // the return value is its attribute mask, and absence is kNo.  Answering kYes
    // with a mask of zero would say "present, with no attributes" -- which is what
    // makes `in` claim every name.
    int attributes = 0;
    switch (intercepted_attributes(isolate, binding->callable, receiver, key.get(), &attributes))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }

    info.GetReturnValue().Set(v8::Integer::New(isolate, attributes));
    return v8::Intercepted::kYes;
}

v8::Intercepted
named_deleter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Boolean>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerNameDel, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript delete of an intercepted property");
    PyRef key(PyRef::adopt(name_key(name, isolate->GetCurrentContext())));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    bool removed = false;
    switch (intercepted_flag(isolate, binding->callable, receiver, key.get(), &removed))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            // A deleter that declares its failure answers false, which is what
            // makes `delete list[0]` silent outside strict mode.
            if (settle_write_failure(isolate, info.ShouldThrowOnError()))
                info.GetReturnValue().Set(v8::Boolean::New(isolate, false));
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }
    info.GetReturnValue().Set(v8::Boolean::New(isolate, removed));
    return v8::Intercepted::kYes;
}

void
named_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerNameEnum, &realm, &binding, &receiver))
        return;

    PyCallback guard("a JavaScript enumeration of an intercepted object");
    trace_event(*realm, TraceEvent::kEnumerate, "names");

    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(binding->callable, receiver, nullptr)));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }
    if (result.get() == Py_None)
    {
        info.GetReturnValue().Set(v8::Array::New(isolate, 0));
        return;
    }

    PyRef sequence(
        PyRef::adopt(PySequence_Fast(result.get(), "a name enumerator must return a sequence")));
    if (!sequence)
    {
        throw_python_error(isolate);
        return;
    }

    const Py_ssize_t count = PySequence_Size(sequence.get());
    if (count < 0)
    {
        throw_python_error(isolate);
        return;
    }

    v8::Local<v8::Array> array = v8::Array::New(isolate, static_cast<int>(count));
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(sequence.get(), index)));
        if (!item)
        {
            throw_python_error(isolate);
            return;
        }
        v8::Local<v8::Value> converted = to_js(item.get(), *realm);
        if (converted.IsEmpty())
        {
            throw_python_error(isolate);
            return;
        }
        if (!array->Set(context, static_cast<uint32_t>(index), converted).FromMaybe(false))
            return;
    }
    info.GetReturnValue().Set(array);
}

v8::Intercepted
indexed_getter(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerIndexGet, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript read of an intercepted index");
    PyRef key(PyRef::adopt(index_key(index)));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kGet, key.get());

    v8::Local<v8::Value> converted;
    switch (intercepted_value(isolate, *realm, binding->callable, receiver, key.get(), &converted))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }
    info.GetReturnValue().Set(converted);
    return v8::Intercepted::kYes;
}

v8::Intercepted
indexed_setter(uint32_t index, v8::Local<v8::Value> value,
               const v8::PropertyCallbackInfo<void>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerIndexSet, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript write to an intercepted index");
    PyRef key(PyRef::adopt(index_key(index)));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kSet, key.get());

    PyRef converted(PyRef::adopt(to_python_boundary(value, *realm)));
    if (!converted)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    // Called directly rather than through call_handler: a failed write may be an
    // operation the host declared failed rather than an error to report, and
    // converting the exception here would leave nothing to inspect.
    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(
        binding->callable, receiver, key.get(), converted.get(), nullptr)));
    if (!result)
    {
        if (settle_write_failure(isolate, info.ShouldThrowOnError()))
            info.GetReturnValue().Set(false);
        return v8::Intercepted::kYes;
    }
    return result.get() == Py_None ? v8::Intercepted::kNo : v8::Intercepted::kYes;
}

v8::Intercepted
indexed_query(uint32_t index, const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerIndexQuery, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript existence test on an intercepted index");
    PyRef key(PyRef::adopt(index_key(index)));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    trace_key(*realm, TraceEvent::kHas, key.get());

    // Same V2 contract as the named form, and the same answer shape: absence is
    // kNo, and a mask of zero means present-with-no-attributes rather than absent.
    int attributes = 0;
    switch (intercepted_attributes(isolate, binding->callable, receiver, key.get(), &attributes))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }

    info.GetReturnValue().Set(v8::Integer::New(isolate, attributes));
    return v8::Intercepted::kYes;
}

v8::Intercepted
indexed_deleter(uint32_t index, const v8::PropertyCallbackInfo<v8::Boolean>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerIndexDel, &realm, &binding, &receiver))
        return v8::Intercepted::kNo;

    PyCallback guard("a JavaScript delete of an intercepted index");
    PyRef key(PyRef::adopt(index_key(index)));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    bool removed = false;
    switch (intercepted_flag(isolate, binding->callable, receiver, key.get(), &removed))
    {
        case Answer::kDeclined:
            return v8::Intercepted::kNo;
        case Answer::kFailed:
            // A deleter that declares its failure answers false, which is what
            // makes `delete list[0]` silent outside strict mode.
            if (settle_write_failure(isolate, info.ShouldThrowOnError()))
                info.GetReturnValue().Set(v8::Boolean::New(isolate, false));
            return v8::Intercepted::kYes;
        case Answer::kAnswered:
            break;
    }
    info.GetReturnValue().Set(v8::Boolean::New(isolate, removed));
    return v8::Intercepted::kYes;
}

namespace
{

// The Python object behind a context's global object, borrowed, or null when that
// realm's global is an ordinary JavaScript object.
PyObject*
global_instance_of(v8::Local<v8::Context> context)
{
    if (context.IsEmpty())
        return nullptr;

    Realm* realm = Realm::from_context(context);
    if (realm == nullptr)
        return nullptr;

    PythonOwner* record = nullptr;
    if (!python_owner_of(context->Global(), *realm, &record))
        return nullptr;
    return record->instance;
}

}  // namespace

bool
cross_origin_access_check(v8::Local<v8::Context> accessing_context,
                          v8::Local<v8::Object> accessed_object, v8::Local<v8::Value> data)
{
    if (data.IsEmpty() || !data->IsExternal())
        return true;

    auto* binding = static_cast<MemberBinding*>(v8c::external_value(data));
    if (binding == nullptr || binding->callable == nullptr)
        return true;

    // GetCurrent rather than a getter on the context: this runs inside a V8
    // callback, so the isolate is the one that is current.
    v8::Isolate* isolate = v8::Isolate::GetCurrent();

    // The object's own realm answers, and it answers about the realm asking: a
    // policy belongs to the page being touched, not to whoever is touching it.  A
    // realm whose global is not a Python object has no policy to state, so the
    // access stands rather than being refused on a guess.
    PyObject* interviewee = global_instance_of(accessed_object->GetCreationContextChecked());
    PyObject* accessor = global_instance_of(accessing_context);
    if (interviewee == nullptr || accessor == nullptr)
        return true;

    PyCallback guard("a cross-origin access check");

    // Reported in the accessing realm, because that is where a refusal surfaces and
    // where the caller can catch it.
    v8::Context::Scope scope(accessing_context);

    PyRef allowed(PyRef::adopt(
        PyObject_CallFunctionObjArgs(binding->callable, interviewee, accessor, nullptr)));
    if (!allowed)
    {
        // AttributeError is how a host says "I have no policy".  The previous
        // binding looked the method up on every call and allowed when it was
        // absent, so a host that models its absence this way keeps working.
        if (PyErr_ExceptionMatches(PyExc_AttributeError))
        {
            PyErr_Clear();
            return true;
        }

        throw_python_error(isolate);
        return false;
    }

    // Only an exact False denies.  A checker that returns None, or nothing at all,
    // is not answering the question, and reading silence as a refusal would make
    // every host that forgot to return into one that denies everything.
    return allowed.get() != Py_False;
}

namespace
{

// Marks a lazy name as being materialized for as long as this lives.
//
// It brackets the factory call as well as the install, because the failure this
// exists to survive -- a factory that reads the name it is building -- happens
// before there is anything to install.
class LazyMaterialization
{
public:
    LazyMaterialization(Realm& realm, const char* name) : realm_(&realm), name_(name)
    {
        realm_->begin_lazy_materialization(name_);
    }

    ~LazyMaterialization() { realm_->end_lazy_materialization(name_); }

    LazyMaterialization(const LazyMaterialization&) = delete;
    LazyMaterialization& operator=(const LazyMaterialization&) = delete;

private:
    Realm* realm_;
    const char* name_;
};

}  // namespace

void
lazy_interface_getter(v8::Local<v8::Name> name, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    auto* binding = static_cast<LazyBinding*>(
        !info.Data().IsEmpty() && info.Data()->IsExternal() ? v8c::external_value(info.Data())
                                                            : nullptr);
    if (binding == nullptr || binding->realm == nullptr || !binding->realm->is_active())
        return;

    Realm& realm = *binding->realm;
    if (realm.lazy_is_materializing(binding->name.c_str()))
    {
        // Something read this name while its own install was still running.  V8 has
        // not cached the property yet at this point, so answering by calling the
        // factory again would recurse until the stack ended.  Undefined is the
        // honest answer: the install in progress is about to define the property,
        // and whatever asked too early sees the real value the next time.
        return;
    }

    PyRef key(PyRef::adopt(name_key(name, context)));
    if (!key)
    {
        throw_python_error(isolate);
        return;
    }

    // Claimed before the factory runs, because a factory that reads the name it is
    // building is the failure this guards against, and it does that on the way in.
    LazyMaterialization materializing(realm, binding->name.c_str());

    // The factory parks whatever error state Python had on entry and restores it on
    // the way out, so a failure below cannot surface at the next unrelated Python
    // statement.
    PyCallback guard("a lazy interface factory");

    PyRef description(PyRef::adopt(
        PyObject_CallFunctionObjArgs(binding->factory, key.get(), nullptr)));
    if (!description)
    {
        throw_python_error(isolate);
        return;
    }
    // "Not this realm's name" is an answer, not a failure: the name stays undefined
    // and V8 caches that, so asking again costs nothing.
    if (description.get() == Py_None)
        return;

    // Answered with what the install produced for this name, which is the one thing
    // the install knows and this does not: whether the name became a constructor or
    // an object.  Read out of the install rather than off the global object, because
    // the property being read is this very one and it is mid-initialization.
    v8::Local<v8::Value> exposed;
    const bool installed =
        install_description(realm, description.get(), binding->name.c_str(), &exposed);
    if (!installed)
    {
        throw_python_error(isolate);
        return;
    }

    // A description that did not expose the name it was asked for is a factory that
    // answered about something else, and V8 is about to cache undefined for the
    // property.  Saying so is better than a name that silently never appears.
    if (exposed.IsEmpty())
    {
        isolate->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8Literal(
            isolate, "a lazy factory built a batch that does not expose the name it was "
                     "asked for")));
        return;
    }
    info.GetReturnValue().Set(exposed);
}

void
indexed_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    Realm* realm = nullptr;
    MemberBinding* binding = nullptr;
    PyObject* receiver = nullptr;
    if (!open_interceptor(info, kHandlerIndexEnum, &realm, &binding, &receiver))
        return;

    PyCallback guard("a JavaScript enumeration of an intercepted object");
    trace_event(*realm, TraceEvent::kEnumerate, "indices");

    PyRef result(PyRef::adopt(PyObject_CallFunctionObjArgs(binding->callable, receiver, nullptr)));
    if (!result)
    {
        throw_python_error(isolate);
        return;
    }

    // A length rather than a sequence: an indexed enumerator that had to build a
    // list to say "there are a thousand" would allocate a thousand entries to
    // answer a question about a number.
    const long long length = PyLong_AsLongLong(result.get());
    if (length == -1 && PyErr_Occurred())
    {
        throw_python_error(isolate);
        return;
    }

    // Capped, because the number comes from the host and an array of a billion
    // indices is not a useful answer to anything.
    constexpr long long kEnumerationLimit = 1 << 20;
    const long long count = length < 0 ? 0 : (length > kEnumerationLimit ? kEnumerationLimit : length);

    v8::Local<v8::Array> array = v8::Array::New(isolate, static_cast<int>(count));
    for (long long index = 0; index < count; ++index)
    {
        if (!array->Set(isolate->GetCurrentContext(), static_cast<uint32_t>(index),
                        v8::Integer::New(isolate, static_cast<int32_t>(index)))
                 .FromMaybe(false))
            return;
    }
    info.GetReturnValue().Set(array);
}

}  // namespace v8n::bridge
