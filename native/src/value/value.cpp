// The value boundary and the Python ownership record behind it.
//
// See include/v8n/value.h for the contract; this file is where the three states
// are decided and where a Python instance keeps its identity across the
// crossing.

#include "v8n/value.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "kernel/v8_compat.h"
#include "value/value_internal.h"
#include "v8n/boundary.h"
#include "v8n/realm.h"

#include <cstring>
#include <new>
#include <string>
#include <vector>

namespace v8n
{
// The primitives and the handle type live in the value layer proper; the
// boundary functions below are the part of it that the rest of the binding
// calls.
using value::is_null_singleton;
using value::is_undefined_singleton;
using value::null_singleton;
using value::undefined_singleton;
using value::value_type;

namespace
{

// -- Python ownership records -------------------------------------------

void
owner_collected(const v8::WeakCallbackInfo<PythonOwner>& info)
{
    PythonOwner* record = info.GetParameter();
    record->wrapper.Reset();
    // Only mark it.  The release runs at the realm's next safe point, because a
    // weak callback may not drop a Python reference: that reference can run a
    // finalizer, and running arbitrary Python with a live context on the stack
    // is the re-entrancy the previous binding had to work around.
    record->wants_release = true;
}

PythonOwner*
owner_of_object(v8::Local<v8::Value> value, Realm& realm)
{
    if (value.IsEmpty() || !value->IsObject())
        return nullptr;

    v8::Isolate* isolate = realm.isolate().v8();
    v8::Local<v8::Value> raw;
    if (!value.As<v8::Object>()
             ->GetPrivate(realm.context(), python_owner_key(isolate))
             .ToLocal(&raw))
        return nullptr;
    if (!raw->IsExternal())
        return nullptr;

    auto* record = static_cast<PythonOwner*>(v8c::external_value(raw));
    // A record only means anything to the realm that made it: the symbol is
    // per isolate, so a foreign realm's object would otherwise be mistaken for
    // one of ours.
    if (record == nullptr || record->realm != &realm)
        return nullptr;
    return record;
}

// -- Python name helpers ------------------------------------------------

PyObject*
python_name(v8::Local<v8::Name> name, v8::Local<v8::Context> context)
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
python_string(v8::Local<v8::String> text, v8::Isolate* isolate)
{
    v8::String::Utf8Value utf8(isolate, text);
    if (*utf8 == nullptr)
        return raise(PyExc_UnicodeDecodeError, "a JavaScript string was not valid UTF-8");
    return PyUnicode_DecodeUTF8(*utf8, utf8.length(), "replace");
}

// -- the generic wrapper ------------------------------------------------

// Named property access on a Python instance with no installed interface.
//
// This is what keeps a plain host object usable: the bridge installs real
// interfaces for the types that have them, and everything else reaches its
// Python attributes through here.
v8::Intercepted
fallback_getter(v8::Local<v8::Name> name,
                const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    Realm* realm = Realm::from_context(context);
    if (realm == nullptr)
        return v8::Intercepted::kNo;
    PythonOwner* record = owner_of_object(info.Holder(), *realm);
    if (record == nullptr)
        return v8::Intercepted::kNo;

    PyRef key(PyRef::adopt(python_name(name, context)));
    if (!key)
    {
        PyErr_Clear();
        return v8::Intercepted::kNo;
    }

    PyRef attribute(PyRef::adopt(PyObject_GetAttr(record->instance, key.get())));
    if (!attribute)
    {
        // A missing attribute is not this interceptor's answer: saying "no"
        // lets V8 continue to the prototype chain and finally produce
        // undefined, which is what JavaScript property access means.
        PyErr_Clear();
        return v8::Intercepted::kNo;
    }

    v8::Local<v8::Value> converted = to_js(attribute.get(), *realm);
    if (converted.IsEmpty())
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }
    info.GetReturnValue().Set(converted);
    return v8::Intercepted::kYes;
}

v8::Intercepted
fallback_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value,
                const v8::PropertyCallbackInfo<void>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    Realm* realm = Realm::from_context(context);
    if (realm == nullptr)
        return v8::Intercepted::kNo;
    PythonOwner* record = owner_of_object(info.Holder(), *realm);
    if (record == nullptr)
        return v8::Intercepted::kNo;

    PyRef key(PyRef::adopt(python_name(name, context)));
    if (!key)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    PyRef converted(PyRef::adopt(to_python_boundary(value, *realm)));
    if (!converted)
    {
        throw_python_error(isolate);
        return v8::Intercepted::kYes;
    }

    if (PyObject_SetAttr(record->instance, key.get(), converted.get()) < 0)
        throw_python_error(isolate);
    return v8::Intercepted::kYes;
}

// A host callable reached from JavaScript.
//
// The record arrives as the function's data: v8::Function::New takes it once
// and V8 hands it back on every call, so this path looks nothing up -- no cache,
// no map keyed by the Python object, and therefore nothing that could grow for
// the life of the process.
void
callable_call(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    Realm* realm = Realm::from_context(context);
    auto* record = static_cast<PythonOwner*>(
        !info.Data().IsEmpty() && info.Data()->IsExternal()
            ? v8c::external_value(info.Data())
            : nullptr);

    if (realm == nullptr || record == nullptr || record->instance == nullptr)
    {
        // The realm is gone, so this function is a leftover JavaScript still
        // holds.  Saying so beats calling a Python callable that close() may
        // already have released.
        isolate->ThrowException(v8::Exception::TypeError(v8::String::NewFromUtf8Literal(
            isolate, "this host callable belongs to a realm that has closed")));
        return;
    }

    // The guard parks whatever error state Python had on entry and restores it
    // on the way out, so a raise below cannot surface at the next unrelated
    // Python statement.  Its own failure path is not used here: this callback
    // has a JavaScript frame to report into, which is better than unraisable.
    PyCallback guard("a JavaScript call into a host callable");

    const int count = info.Length();
    PyRef arguments(PyRef::adopt(PyTuple_New(count)));
    if (!arguments)
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
        // Steals the reference; the tuple owns it from here on.
        PyTuple_SetItem(arguments.get(), index, item.release());
    }

    PyRef result(PyRef::adopt(PyObject_CallObject(record->instance, arguments.get())));
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

v8::Intercepted
fallback_query(v8::Local<v8::Name> name,
               const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::Isolate* isolate = info.GetIsolate();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    Realm* realm = Realm::from_context(context);
    if (realm == nullptr)
        return v8::Intercepted::kNo;
    PythonOwner* record = owner_of_object(info.Holder(), *realm);
    if (record == nullptr)
        return v8::Intercepted::kNo;

    PyRef key(PyRef::adopt(python_name(name, context)));
    if (!key)
    {
        PyErr_Clear();
        return v8::Intercepted::kNo;
    }

    const int present = PyObject_HasAttr(record->instance, key.get());
    if (present < 0)
    {
        PyErr_Clear();
        return v8::Intercepted::kNo;
    }
    if (present != 1)
        return v8::Intercepted::kNo;

    // Answering "present" is what stops V8 from falling back to the prototype
    // chain and losing an attribute that exists only on the instance.  A zero
    // attribute mask means "exists, no special flags", which is what a plain
    // Python attribute is.
    info.GetReturnValue().Set(v8::Integer::New(isolate, 0));
    return v8::Intercepted::kYes;
}

// -- Python containers ---------------------------------------------------

// A Python container crosses as the JavaScript container with the same shape:
// a list or tuple becomes an array, a dict becomes a plain object.
//
// Without this a host that answers a query with a structure would hand over a
// generic host object instead -- one whose "properties" are looked up as Python
// attributes, so a dict's keys are invisible to JavaScript and its methods are
// what `obj.get` finds.  That is not a shape any caller can use, and it is not
// what the previous binding produced either: it converted both containers.
//
// Recursion is bounded by the structure itself, which is what a host that hands
// over a self-referential list deserves; the alternative would be an identity
// map like the one materialisation keeps, and that is a cost on every crossing
// to serve a shape almost nothing produces.
v8::Local<v8::Value>
container_to_js(PyObject* value, Realm& realm)
{
    v8::Isolate* isolate = realm.isolate().v8();
    v8::Local<v8::Context> context = realm.context();
    v8::Local<v8::Value> empty;

    if (PyDict_Check(value))
    {
        v8::Local<v8::Object> object = v8::Object::New(isolate);
        PyObject* key = nullptr;
        PyObject* item = nullptr;
        Py_ssize_t position = 0;
        // PyDict_Next borrows both, so the loop owns nothing and cannot leak.
        while (PyDict_Next(value, &position, &key, &item))
        {
            v8::Local<v8::Value> name = to_js(key, realm);
            if (name.IsEmpty())
                return empty;
            v8::Local<v8::Value> converted = to_js(item, realm);
            if (converted.IsEmpty())
                return empty;
            if (!object->Set(context, name, converted).FromMaybe(false))
            {
                raise(PyExc_RuntimeError, "failed to build a JavaScript object from a dict");
                return empty;
            }
        }
        return object;
    }

    // Sized and indexed through the exported functions: the limited API hides
    // the macros that read a list or tuple's storage directly.
    const bool is_list = PyList_Check(value) == 1;
    const Py_ssize_t size = is_list ? PyList_Size(value) : PyTuple_Size(value);
    if (size < 0)
        return empty;

    v8::Local<v8::Array> array =
        v8::Array::New(isolate, static_cast<int>(size));
    for (Py_ssize_t index = 0; index < size; ++index)
    {
        PyObject* item = is_list ? PyList_GetItem(value, index)
                                 : PyTuple_GetItem(value, index);
        if (item == nullptr)
            return empty;
        v8::Local<v8::Value> converted = to_js(item, realm);
        if (converted.IsEmpty())
            return empty;
        if (!array->Set(context, static_cast<uint32_t>(index), converted)
                 .FromMaybe(false))
        {
            raise(PyExc_RuntimeError, "failed to build a JavaScript array from a sequence");
            return empty;
        }
    }
    return array;
}

// -- deep materialisation ----------------------------------------------

// One conversion, with the identity map that makes cycles and shared
// references survive it.
class Materializer
{
public:
    explicit Materializer(Realm& realm) : realm_(realm) {}

    PyObject* convert(v8::Local<v8::Value> value)
    {
        if (value.IsEmpty())
        {
            raise(PyExc_RuntimeError, "cannot materialise an empty handle");
            return nullptr;
        }

        if (!value->IsObject())
            return to_python_boundary(value, realm_);

        v8::Local<v8::Object> object = value.As<v8::Object>();
        if (python_owner_of(object, realm_, nullptr))
            return to_python_boundary(object, realm_);

        PyObject* existing = find(object);
        if (existing != nullptr)
            return PyRef::borrow(existing).release();

        if (object->IsArray())
            return convert_array(object);
        if (is_plain_object(object))
            return convert_object(object);
        // Functions, typed arrays, map/set and anything with a custom prototype
        // stay handles: expanding them would either run host code or produce a
        // structure that cannot be turned back into the same object.
        return to_python_boundary(object, realm_);
    }

private:
    struct Seen
    {
        int hash = 0;
        v8::Global<v8::Value> key;
        PyObject* result = nullptr;
    };

    v8::Isolate* isolate() const { return realm_.isolate().v8(); }

    bool is_plain_object(v8::Local<v8::Object> object)
    {
        v8::Local<v8::Context> context = realm_.context();
        if (object->HasOwnProperty(context, v8::Symbol::GetToStringTag(isolate()))
                .FromMaybe(true))
            return false;
        return object->GetPrototype()->StrictEquals(
            context->GetEmbedderData(kObjectPrototypeSlot));
    }

    PyObject* find(v8::Local<v8::Object> object)
    {
        const int hash = object->GetIdentityHash();
        for (const Seen& entry : seen_)
        {
            if (entry.hash != hash)
                continue;
            if (entry.key.Get(isolate())->StrictEquals(object))
                return entry.result;
        }
        return nullptr;
    }

    bool remember(v8::Local<v8::Object> value, PyObject* result)
    {
        Seen entry;
        entry.hash = value->GetIdentityHash();
        entry.key.Reset(isolate(), value);
        entry.result = result;
        seen_.push_back(std::move(entry));
        return true;
    }

    PyObject* convert_array(v8::Local<v8::Value> value)
    {
        v8::Local<v8::Array> array = value.As<v8::Array>();
        v8::Local<v8::Context> context = realm_.context();

        PyRef list(PyRef::adopt(PyList_New(array->Length())));
        if (!list)
            return nullptr;
        remember(array, list.get());

        const uint32_t length = array->Length();
        for (uint32_t index = 0; index < length; ++index)
        {
            v8::Local<v8::Value> item;
            if (!array->Get(context, index).ToLocal(&item))
            {
                raise(PyExc_RuntimeError, "failed to read an array element");
                return nullptr;
            }
            PyRef converted(PyRef::adopt(convert(item)));
            if (!converted)
                return nullptr;
            // PyList_SetItem steals the reference.
            PyList_SetItem(list.get(), index, converted.release());
        }
        return list.release();
    }

    PyObject* convert_object(v8::Local<v8::Value> value)
    {
        v8::Local<v8::Object> object = value.As<v8::Object>();
        v8::Local<v8::Context> context = realm_.context();

        v8::Local<v8::Array> keys;
        if (!object->GetOwnPropertyNames(
                 context,
                 static_cast<v8::PropertyFilter>(v8::ONLY_ENUMERABLE | v8::SKIP_SYMBOLS),
                 v8::KeyConversionMode::kConvertToString)
                 .ToLocal(&keys))
        {
            raise(PyExc_RuntimeError, "failed to enumerate an object");
            return nullptr;
        }

        PyRef dict(PyRef::adopt(PyDict_New()));
        if (!dict)
            return nullptr;
        remember(object, dict.get());

        for (uint32_t index = 0; index < keys->Length(); ++index)
        {
            v8::Local<v8::Value> key;
            if (!keys->Get(context, index).ToLocal(&key))
            {
                raise(PyExc_RuntimeError, "failed to read an object key");
                return nullptr;
            }
            PyRef py_key(PyRef::adopt(convert(key)));
            if (!py_key)
                return nullptr;

            v8::Local<v8::Value> item;
            if (!object->Get(context, key).ToLocal(&item))
            {
                raise(PyExc_RuntimeError, "failed to read an object property");
                return nullptr;
            }
            PyRef py_item(PyRef::adopt(convert(item)));
            if (!py_item)
                return nullptr;

            if (PyDict_SetItem(dict.get(), py_key.get(), py_item.get()) < 0)
                return nullptr;
        }
        return dict.release();
    }

    Realm& realm_;
    std::vector<Seen> seen_;
};

}  // namespace

// -- the public boundary -----------------------------------------------

v8::Local<v8::Private>
python_owner_key(v8::Isolate* isolate)
{
    // ForApi interns the symbol in the isolate's API private symbol table, so
    // every call returns the same one and there is nothing to keep alive or to
    // release when the realm closes.
    return v8::Private::ForApi(
        isolate, v8::String::NewFromUtf8Literal(isolate, "v8n.python_owner"));
}

bool
python_owner_of(v8::Local<v8::Value> value, Realm& realm, PythonOwner** owner)
{
    PythonOwner* record = owner_of_object(value, realm);
    if (record == nullptr)
        return false;
    if (owner != nullptr)
        *owner = record;
    return true;
}

void
release_python_owner(PythonOwner& owner)
{
    if (!owner.wrapper.IsEmpty())
        owner.wrapper.Reset();

    PyObject* instance = owner.instance;
    owner.instance = nullptr;
    delete &owner;

    // Released last, and outside the record's lifetime: a finalizer that runs
    // here must not find a half dismantled record through the wrapper.
    Py_XDECREF(instance);
}

void
discard_python_owner(PythonOwner& owner)
{
    PyObject* instance = owner.instance;
    owner.instance = nullptr;

    // Deliberately not `delete &owner`: that would run ~v8::Global, which
    // releases a handle in an isolate this thread must not touch.  Releasing the
    // storage without running the destructor leaks exactly the handle and
    // nothing else, which is the trade documented in value.h.
    ::operator delete(&owner);

    Py_XDECREF(instance);
}

namespace
{

// A record, registered with its realm and holding the one Python reference that
// keeps the instance alive.  Returns null with a Python error set.
PythonOwner*
new_owner(Realm& realm, PyObject* instance)
{
    auto* record = new (std::nothrow) PythonOwner();
    if (record == nullptr)
    {
        PyErr_NoMemory();
        return nullptr;
    }

    Py_INCREF(instance);
    record->instance = instance;
    record->strong = true;

    // Registered before the weak handle is armed: if the JavaScript value were
    // collected first, the callback would mark a record the realm has never seen.
    realm.attach(record);
    return record;
}

// Makes the record reachable from the object and arms the weak handle that
// reports the object's collection.  Owns the record on failure.
bool
arm_owner(Realm& realm, v8::Local<v8::Object> wrapper, PythonOwner* record)
{
    v8::Isolate* isolate = realm.isolate().v8();

    // The record is reachable from the value before the weak handle is armed, so
    // a collection in between cannot produce a value whose owner is missing.
    if (!wrapper->SetPrivate(realm.context(), python_owner_key(isolate),
                             v8c::external_new(isolate, record))
             .FromMaybe(false))
    {
        realm.detach(record);
        release_python_owner(*record);
        return false;
    }

    record->wrapper.Reset(isolate, wrapper);
    record->wrapper.SetWeak(record, owner_collected, v8::WeakCallbackType::kParameter);
    return true;
}

}  // namespace

bool
adopt_python_object(Realm& realm, v8::Local<v8::Object> object, PyObject* instance)
{
    if (object.IsEmpty() || instance == nullptr)
    {
        raise(PyExc_RuntimeError, "a host instance needs an object to adopt it");
        return false;
    }

    PythonOwner* record = new_owner(realm, instance);
    if (record == nullptr)
        return false;
    if (!arm_owner(realm, object, record))
    {
        if (!PyErr_Occurred())
            raise(PyExc_RuntimeError, "failed to attach a host instance to an object");
        return false;
    }
    return true;
}

v8::Local<v8::Value>
wrap_python_object(Realm& realm, PyObject* instance)
{
    v8::Isolate* isolate = realm.isolate().v8();

    // Identity first: a host value that makes the round trip twice has to come
    // back as the same JavaScript value, or `==` in JavaScript stops matching
    // what Python means by the same object.
    for (PythonOwner* record = realm.owners(); record != nullptr; record = record->next)
    {
        if (record->instance == instance && !record->wrapper.IsEmpty())
            return record->wrapper.Get(isolate);
    }

    // A host object of a type that has an interface arrives as an instance of
    // that interface, callable or not.  The interface owns the shape -- the
    // prototype, the members, whether JavaScript sees the object at all -- and
    // an interface whose instances are meant to be called says so with a call
    // binding of its own.  A callable with no interface is the one case with no
    // shape to borrow, and there a bare function is exactly what Python has.
    PythonOwner* record = new_owner(realm, instance);
    if (record == nullptr)
        return v8::Local<v8::Value>();

    // The host may know this type's interface and simply not have installed it
    // yet, which is what a host that builds interfaces on demand has done for
    // the first object of every type.  Asking is what keeps the object from
    // arriving as its base's interface instead of its own -- the lookup walks
    // the chain, so an unregistered derived type would otherwise be wrapped as
    // whatever ancestor happened to be installed.
    if (!realm.has_interface_for(Py_TYPE(instance)))
        realm.resolve_type(instance);

    v8::Local<v8::Object> wrapper;
    bool built = false;
    v8::Local<v8::ObjectTemplate> template_ = realm.template_for_python_type(instance);
    if (template_.IsEmpty() && PyCallable_Check(instance) == 1)
    {
        // The record is created before the function it describes, because
        // v8::Function::New takes it as the callback's data.  That is also why
        // the function needs no lookup to find its own Python callable.
        v8::Local<v8::Function> function;
        built = v8::Function::New(realm.context(), callable_call,
                                  v8c::external_new(isolate, record), 0,
                                  v8::ConstructorBehavior::kThrow)
                    .ToLocal(&function);
        if (built)
        {
            // A host function carries the name it has in the host.  A page reads
            // ``.name`` and ``String(f)`` on it like any other function, and an
            // anonymous one would say nothing about where it came from.
            PyRef name(PyRef::adopt(PyObject_GetAttrString(instance, "__name__")));
            v8::Local<v8::Value> text;
            if (name)
                text = to_js(name.get(), realm);
            else
                PyErr_Clear();
            if (!text.IsEmpty() && text->IsString())
                function->SetName(text.As<v8::String>());
            wrapper = function;
        }
    }
    else
    {
        // The interface's own template when the instance's type has one, so the
        // object carries the interface's prototype and its members; the realm's
        // generic template otherwise, which still lets a plain host object make
        // the round trip.
        if (template_.IsEmpty())
            template_ = realm.fallback_template(isolate);

        v8::Local<v8::Object> object;
        if (!template_.IsEmpty())
            built = template_->NewInstance(realm.context()).ToLocal(&object);
        if (built)
            wrapper = object;
    }

    if (!built)
    {
        realm.detach(record);
        release_python_owner(*record);
        if (!PyErr_Occurred())
            raise(PyExc_RuntimeError, "failed to create a wrapper for a Python object");
        return v8::Local<v8::Value>();
    }

    if (!arm_owner(realm, wrapper, record))
    {
        if (!PyErr_Occurred())
            raise(PyExc_RuntimeError, "failed to record the owner of a Python object");
        return v8::Local<v8::Value>();
    }
    return wrapper;
}

PyObject*
to_python_boundary(v8::Local<v8::Value> value, Realm& realm)
{
    v8::Isolate* isolate = realm.isolate().v8();

    if (value.IsEmpty())
        return raise(PyExc_RuntimeError, "cannot convert an empty handle");
    if (value->IsNull())
        return PyRef::borrow(null_singleton()).release();
    if (value->IsUndefined())
        return PyRef::borrow(undefined_singleton()).release();
    if (value->IsBoolean())
        return PyBool_FromLong(value.As<v8::Boolean>()->Value() ? 1 : 0);
    if (value->IsString())
        return python_string(value.As<v8::String>(), isolate);
    if (value->IsInt32() || value->IsUint32())
        return PyLong_FromLongLong(value.As<v8::Int32>()->Value());
    if (value->IsNumber())
        return PyFloat_FromDouble(value.As<v8::Number>()->Value());
    // A BigInt deliberately falls through to the wrapper below.  It has no Python
    // counterpart: the int it holds only fits while it is small, and the two
    // languages keep them apart -- ``Number(1n)`` is a TypeError in JavaScript,
    // while a Python int is a perfectly good Web IDL number, so a host handed an
    // int could not tell which language's conversion rule to apply.  Reading one
    // is ``int(handle.to_string())``.
    if (value->IsObject())
    {
        PythonOwner* record = nullptr;
        if (python_owner_of(value, realm, &record))
            return PyRef::borrow(record->instance).release();
    }
    return value_wrapper(realm, value);
}

PyObject*
to_python(v8::Local<v8::Value> value, Realm& realm)
{
    Materializer materializer(realm);
    return materializer.convert(value);
}

v8::Local<v8::Value>
to_js(PyObject* value, Realm& realm)
{
    v8::Isolate* isolate = realm.isolate().v8();
    v8::Local<v8::Value> empty;

    if (value == nullptr)
        return v8::Local<v8::Value>();
    if (is_null_singleton(value) || value == Py_None)
        return v8::Null(isolate);
    if (is_undefined_singleton(value))
        return v8::Undefined(isolate);
    if (PyBool_Check(value))
        return v8::Boolean::New(isolate, value == Py_True);

    if (PyLong_Check(value))
    {
        const long long integer = PyLong_AsLongLong(value);
        if (!PyErr_Occurred() && integer >= INT32_MIN && integer <= INT32_MAX)
            return v8::Integer::New(isolate, static_cast<int32_t>(integer));

        PyErr_Clear();
        const double number = PyLong_AsDouble(value);
        if (PyErr_Occurred())
            return empty;
        return v8::Number::New(isolate, number);
    }

    if (PyFloat_Check(value))
        return v8::Number::New(isolate, PyFloat_AsDouble(value));

    if (PyUnicode_Check(value))
    {
        Py_ssize_t size = 0;
        const char* text = abi3::Utf8(value, &size);
        if (text == nullptr)
            return empty;
        v8::Local<v8::String> string;
        if (!v8::String::NewFromUtf8(isolate, text, v8::NewStringType::kNormal,
                                     static_cast<int>(size))
                 .ToLocal(&string))
        {
            raise(PyExc_RuntimeError, "failed to build a JavaScript string");
            return empty;
        }
        return string;
    }

    if (PyBytes_Check(value) || PyByteArray_Check(value))
    {
        char* data = nullptr;
        Py_ssize_t size = 0;
        if (PyBytes_Check(value))
        {
            if (PyBytes_AsStringAndSize(value, &data, &size) < 0)
                return empty;
        }
        else
        {
            data = PyByteArray_AsString(value);
            size = PyByteArray_Size(value);
            if (data == nullptr || size < 0)
                return empty;
        }
        v8::Local<v8::ArrayBuffer> buffer = v8::ArrayBuffer::New(isolate, static_cast<size_t>(size));
        if (size > 0)
            std::memcpy(buffer->Data(), data, static_cast<size_t>(size));
        return buffer;
    }

    // A handle gives back exactly the value it retained, so an object that
    // travelled out and back is the same object in JavaScript.
    if (Py_TYPE(value) == value_type)
    {
        auto* wrapper = reinterpret_cast<ValueObject*>(value);
        if (wrapper->realm != &realm || wrapper->slot == nullptr)
        {
            raise(PyExc_RuntimeError, "a handle can only be used by the realm that made it");
            return empty;
        }
        return wrapper->slot->get(isolate);
    }

    if (PyList_Check(value) || PyTuple_Check(value) || PyDict_Check(value))
        return container_to_js(value, realm);

    return wrap_python_object(realm, value);
}

}  // namespace v8n

namespace v8n::value
{

v8::Local<v8::ObjectTemplate>
make_fallback_template(v8::Isolate* isolate)
{
    v8::Local<v8::ObjectTemplate> template_ = v8::ObjectTemplate::New(isolate);
    if (template_.IsEmpty())
    {
        raise(PyExc_RuntimeError, "failed to create the host object template");
        return v8::Local<v8::ObjectTemplate>();
    }

    // No deleter: deleting a JavaScript property would have to mean deleting a
    // Python attribute, which is not what `delete obj.x` means to a host that
    // never declared one.  No enumerator either: V8's own own-property listing
    // is what a host wants for a plain object.
    //
    // kNonMasking is what makes the query callback's "no" meaningful: the
    // interceptor answers only for names the Python instance actually has, and
    // everything else keeps resolving through the prototype chain as usual.
    // A masking interceptor claims to own every name, which is not what a host
    // object is.
    v8::NamedPropertyHandlerConfiguration handlers(
        fallback_getter, fallback_setter, fallback_query,
        static_cast<v8::NamedPropertyDeleterCallback>(nullptr),
        static_cast<v8::NamedPropertyEnumeratorCallback>(nullptr),
        v8::Local<v8::Value>(), v8::PropertyHandlerFlags::kNonMasking);
    template_->SetHandler(handlers);
    return template_;
}

}  // namespace v8n::value
