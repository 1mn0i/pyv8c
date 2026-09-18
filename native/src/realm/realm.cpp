#include "v8n/realm.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "bridge/bridge_internal.h"
#include "kernel/handle_slot.h"
#include "kernel/v8_compat.h"
#include "realm/realm_internal.h"
#include "realm/resources.h"
#include "value/value_internal.h"
#include "v8n/boundary.h"
#include "v8n/kernel.h"
#include "v8n/value.h"

#include <algorithm>
#include <new>

namespace v8n
{

// Deletes a realm on a failure path.
//
// The destructor preserves whatever error state it finds, so there is nothing to
// park here.  The name stays because these call sites are exactly where "this
// delete is allowed to happen with an error pending" needs saying, and because
// that is the property the destructor has to keep.
void
Realm::destroy_after_failure(Realm* realm)
{
    delete realm;
}

Realm*
Realm::create(Isolate* isolate)
{
    return create(isolate, RealmOptions());
}

Realm*
Realm::create(Isolate* isolate, const RealmOptions& options)
{
    if (isolate == nullptr)
    {
        raise(PyExc_RuntimeError, "a realm needs an isolate");
        return nullptr;
    }
    if (!isolate->require_owner_thread("creating a realm"))
        return nullptr;

    auto* realm = new (std::nothrow) Realm();
    if (realm == nullptr)
    {
        PyErr_NoMemory();
        return nullptr;
    }
    realm->isolate_ = isolate;
    realm->name_storage_ = options.name == nullptr ? "realm" : options.name;
    realm->name_ = realm->name_storage_.c_str();

    // The registry exists from the start, even if nothing registers a root: a
    // feature that installs a hook should not have to ask whether it can.
    realm->registry_.reset(new (std::nothrow) ResourceRegistry());
    if (realm->registry_ == nullptr)
    {
        PyErr_NoMemory();
        destroy_after_failure(realm);
        return nullptr;
    }

    IsolateEntry entry(*isolate);
    if (!entry.ok())
    {
        destroy_after_failure(realm);
        return nullptr;
    }

    v8::Isolate* v8 = isolate->v8();
    v8::HandleScope handle_scope(v8);

    // A build that cannot verify the microtask queue's layout is not a reason to
    // refuse the realm: the realm works, promises simply never settle, and
    // microtasks_supported() reports the gap so a host can find out before it
    // depends on it.  See microtask.cpp.
    realm->microtasks_enabled_ = options.microtasks && microtasks_available();

    // A batch is read and its templates are built before the context exists,
    // because a realm whose global object is Python needs the global interface's
    // template at Context::New.  V8 takes the global template there and offers no
    // way to give the global object an interceptor afterwards.
    //
    // This is why Realm::create knows about a batch at all.  The alternative --
    // creating the realm and installing later -- cannot express a Python global
    // object, which is the whole reason pyv8c calls a realm a window.
    std::unique_ptr<bridge::Batch> batch;
    v8::Local<v8::ObjectTemplate> global_template;
    if (options.description != nullptr && options.description != Py_None)
    {
        batch.reset(new (std::nothrow) bridge::Batch());
        if (batch == nullptr)
        {
            PyErr_NoMemory();
            delete realm;
            return nullptr;
        }
        if (!bridge::parse_batch(*realm, options.description, batch.get()) ||
            !bridge::build_templates(*isolate, *batch, options.global_this))
        {
            delete realm;
            return nullptr;
        }

        bridge::ParsedInterface* global = batch->global_interface(options.global_this);
        if (global != nullptr)
        {
            if (options.global_this == nullptr)
            {
                raise(PyExc_RuntimeError,
                      "%s is marked global_this but no global object was supplied",
                      global->spec.name);
                destroy_after_failure(realm);
                return nullptr;
            }
            global_template = global->template_slot->get(v8)->InstanceTemplate();
        }
    }

    // Both halves or neither.  A Python global object without a global interface
    // would be an object nothing dispatched through, which reads as "the host
    // object stopped working" rather than as a description mistake.
    if (options.global_this != nullptr && global_template.IsEmpty())
    {
        raise(PyExc_RuntimeError,
              "a global object was supplied but no interface in the batch is marked "
              "global_this, so nothing would reach it");
        destroy_after_failure(realm);
        return nullptr;
    }

    v8::Local<v8::Context> context = v8::Context::New(
        v8, nullptr, v8::MaybeLocal<v8::ObjectTemplate>(global_template),
        v8::MaybeLocal<v8::Value>(), v8::DeserializeInternalFieldsCallback(), nullptr);
    if (context.IsEmpty())
    {
        raise(PyExc_RuntimeError, "failed to create a V8 context");
        destroy_after_failure(realm);
        return nullptr;
    }

    // The context carries its realm so any callback V8 hands back can find the
    // layer it belongs to without a registry lookup, and so the materialiser can
    // recognise a plain object by prototype instead of asking the bridge.
    //
    // Entering the context first is not optional: Object::New and GetPrototype
    // both resolve through the current context, and a bare isolate entry has
    // none.
    context->SetEmbedderData(kRealmDataSlot, v8c::external_new(isolate->v8(), realm));
    {
        v8::Context::Scope context_scope(context);
        context->SetEmbedderData(kObjectPrototypeSlot,
                                 v8::Object::New(isolate->v8())->GetPrototype());
    }

    realm->context_.Reset(v8, context);
    realm->timeout_ms_ = options.timeout_ms;

    {
        v8::Context::Scope context_scope(context);

        // The Python object becomes the global object here rather than being
        // wrapped as one: V8 has already made the object from the global
        // interface's template, so it carries the handlers and the members, and
        // all that is missing is the Python identity behind it.
        if (options.global_this != nullptr && !global_template.IsEmpty())
        {
            if (!adopt_python_object(*realm, context->Global(), options.global_this))
            {
                delete realm;
                return nullptr;
            }
        }

        // Everything that needs a context: the functions, the static members, the
        // names.
        if (batch != nullptr)
        {
            if (!bridge::finish_batch(*realm, context, *batch))
            {
                delete realm;
                return nullptr;
            }
            realm->own_state(batch.release(), bridge::release_batch);
        }
    }

    return realm;
}

PyObject*
Realm::global_object()
{
    if (!require_active("global_object"))
        return nullptr;

    IsolateEntry entry(*isolate_);
    if (!entry.ok())
        return nullptr;

    v8::HandleScope handle_scope(entry.v8());
    v8::Local<v8::Context> local = context();
    if (local.IsEmpty())
        return raise(PyExc_RuntimeError, "the realm has no context");

    return value_wrapper(*this, local->Global());
}

PyObject*
Realm::wrap(PyObject* object)
{
    if (!require_active("wrap"))
        return nullptr;
    if (object == nullptr || object == Py_None)
        return raise(PyExc_TypeError, "wrap() needs an object to wrap");

    IsolateEntry entry(*isolate_);
    if (!entry.ok())
        return nullptr;

    v8::HandleScope handle_scope(entry.v8());
    v8::Local<v8::Context> local = context();
    if (local.IsEmpty())
        return raise(PyExc_RuntimeError, "the realm has no context");
    v8::Context::Scope context_scope(local);

    // Identity is the wrapper's: an object that already crossed gets the same
    // JavaScript value back, which is what makes a handle usable as a reference
    // to something JavaScript holds rather than as a copy of it.
    v8::Local<v8::Value> wrapped = wrap_python_object(*this, object);
    if (wrapped.IsEmpty())
        return nullptr;
    return value_wrapper(*this, wrapped);
}

Realm*
Realm::from_context(v8::Local<v8::Context> context)
{
    if (context.IsEmpty())
        return nullptr;
    return v8c::context_embedder_pointer<Realm>(context, kRealmDataSlot);
}

v8::Local<v8::Context>
Realm::context() const
{
    if (context_.IsEmpty() || isolate_ == nullptr)
        return v8::Local<v8::Context>();
    return context_.Get(isolate_->v8());
}

bool
Realm::require_active(const char* operation) const
{
    if (state_ != State::kActive)
    {
        raise(PyExc_RuntimeError, "%s: the realm is closed", operation);
        return false;
    }
    return true;
}

void
Realm::own_state(void* state, void (*release)(void*))
{
    if (state == nullptr)
        return;
    OwnedState entry;
    entry.state = state;
    entry.release = release;
    owned_states_.push_back(entry);
}

bool
Realm::lazy_is_materializing(const char* name) const
{
    if (name == nullptr)
        return false;
    for (const std::string& pending : lazy_materializing_)
    {
        if (pending == name)
            return true;
    }
    return false;
}

void
Realm::begin_lazy_materialization(const char* name)
{
    if (name != nullptr)
        lazy_materializing_.emplace_back(name);
}

void
Realm::end_lazy_materialization(const char* name)
{
    if (name == nullptr || lazy_materializing_.empty())
        return;
    // The stack unwinds, so the entry that goes is the last one.  Matching by name
    // rather than trusting the back would silently drop somebody else's entry if the
    // two ever disagreed, and a stuck entry means a name that can never be built.
    for (std::size_t index = lazy_materializing_.size(); index > 0; --index)
    {
        if (lazy_materializing_[index - 1] == name)
        {
            lazy_materializing_.erase(lazy_materializing_.begin() +
                                      static_cast<std::ptrdiff_t>(index - 1));
            return;
        }
    }
}

bool
Realm::register_root(PyObject** slot)
{
    if (registry_ == nullptr)
    {
        raise(PyExc_RuntimeError, "the realm has no resource registry");
        return false;
    }
    return registry_->add(slot);
}

void
Realm::unregister_root(PyObject** slot)
{
    if (registry_ != nullptr)
        registry_->remove(slot);
}

std::size_t
Realm::root_count() const
{
    return registry_ == nullptr ? 0 : registry_->size();
}

int
Realm::traverse(visitproc visit, void* arg) const
{
    // The borrowed wrapper reference is visited but not registered: the realm
    // does not own it, so closing must not release it, yet a cycle through it
    // still has to be visible to the collector.
    if (python_object_ != nullptr)
    {
        const int result = visit(python_object_, arg);
        if (result != 0)
            return result;
    }
    if (registry_ == nullptr)
        return 0;
    return registry_->traverse(visit, arg);
}

v8::Local<v8::ObjectTemplate>
Realm::fallback_template(v8::Isolate* isolate)
{
    if (fallback_template_ == nullptr)
    {
        auto* slot = new (std::nothrow) HandleSlot<v8::ObjectTemplate>();
        if (slot == nullptr)
        {
            PyErr_NoMemory();
            return v8::Local<v8::ObjectTemplate>();
        }
        fallback_template_ = slot;

        v8::Local<v8::ObjectTemplate> template_ = value::make_fallback_template(isolate);
        if (template_.IsEmpty())
            return v8::Local<v8::ObjectTemplate>();
        slot->emplace(*isolate_, template_);
    }
    return fallback_template_->get(isolate);
}

namespace
{

// The name of a Python type, for an error message.  tp_name is not reachable
// through the limited API, so it is fetched the way anything else would be.
std::string
type_name_of(PyTypeObject* type)
{
    PyRef name(PyRef::adopt(
        PyObject_GetAttrString(reinterpret_cast<PyObject*>(type), "__name__")));
    if (!name)
    {
        PyErr_Clear();
        return "a Python type";
    }
    const char* text = abi3::Utf8(name.get());
    if (text == nullptr)
    {
        PyErr_Clear();
        return "a Python type";
    }
    return text;
}

}  // namespace

bool
Realm::register_python_type(PyTypeObject* type,
                            v8::Local<v8::FunctionTemplate> interface_template)
{
    if (type == nullptr || interface_template.IsEmpty())
    {
        raise(PyExc_RuntimeError, "a Python type registration needs a type and a template");
        return false;
    }

    for (const RegisteredType& entry : registered_types_)
    {
        if (entry.type == type)
        {
            raise(PyExc_ValueError, "%s is already the Python type of an interface",
                  type_name_of(type).c_str());
            return false;
        }
    }

    auto* slot = new (std::nothrow) HandleSlot<v8::FunctionTemplate>();
    if (slot == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }
    slot->emplace(*isolate_, interface_template);

    RegisteredType entry;
    entry.type = type;
    entry.template_slot = slot;
    registered_types_.push_back(entry);

    // A type written off earlier as having no interface may have one now, and the
    // next crossing has to find it.
    unregistered_types_.erase(
        std::remove(unregistered_types_.begin(), unregistered_types_.end(), type),
        unregistered_types_.end());
    return true;
}

bool
Realm::set_type_resolver(PyObject* callable)
{
    // Same reason as the error hooks: after a close there is no root set left to
    // hold it, and a resolver installed now would never be released.
    if (!require_active("set_type_resolver"))
        return false;

    if (callable != nullptr && !PyCallable_Check(callable))
    {
        raise(PyExc_TypeError, "the type resolver must be callable");
        return false;
    }

    if (callable != nullptr && !register_root(&type_resolver_))
        return false;

    PyObject* previous = type_resolver_;
    type_resolver_ = callable;
    Py_XINCREF(type_resolver_);
    Py_XDECREF(previous);
    return true;
}

void
Realm::resolve_type(PyObject* instance)
{
    PyObject* resolver = type_resolver_;
    if (resolver == nullptr || instance == nullptr)
        return;

    // A resolver that fails must not break the crossing that asked for it: the
    // object is still wrapped, just as a generic host object, which is what the
    // caller would have got without a resolver at all.
    PyCallback guard("a type resolver");
    PyRef result(PyRef::adopt(
        PyObject_CallFunctionObjArgs(resolver, instance, nullptr)));
    if (!result)
        PyErr_WriteUnraisable(resolver);
}

bool
Realm::register_interface_template(const char* name,
                                   v8::Local<v8::FunctionTemplate> template_)
{
    if (name == nullptr || template_.IsEmpty())
    {
        raise(PyExc_RuntimeError, "an interface registration needs a name and a template");
        return false;
    }

    // A second install of the same interface is a second description of one
    // interface: the realm already has the constructor JavaScript is using, and
    // an inheritance edge has to point at that one rather than at a copy.
    for (const InstalledInterface& entry : installed_interfaces_)
    {
        if (entry.name == name)
            return true;
    }

    auto* slot = new (std::nothrow) HandleSlot<v8::FunctionTemplate>();
    if (slot == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }
    if (!slot->emplace(*isolate_, template_))
    {
        delete slot;
        return false;
    }

    InstalledInterface entry;
    entry.name = name;
    entry.template_slot = slot;
    installed_interfaces_.push_back(std::move(entry));
    return true;
}

v8::Local<v8::FunctionTemplate>
Realm::installed_interface(const char* name) const
{
    if (name == nullptr || isolate_ == nullptr)
        return v8::Local<v8::FunctionTemplate>();

    // Only valid on the owner thread, like every other use of the isolate.
    if (!isolate_->is_owner_thread())
        return v8::Local<v8::FunctionTemplate>();

    for (const InstalledInterface& entry : installed_interfaces_)
    {
        if (entry.name == name)
            return entry.template_slot->get(isolate_->v8());
    }
    return v8::Local<v8::FunctionTemplate>();
}

bool
Realm::has_interface_for(PyTypeObject* type) const
{
    if (type == nullptr)
        return false;
    for (const RegisteredType& entry : registered_types_)
    {
        if (entry.type == type)
            return true;
    }
    return false;
}

v8::Local<v8::ObjectTemplate>
Realm::template_for_python_type(PyObject* instance)
{
    if (instance == nullptr || registered_types_.empty())
        return v8::Local<v8::ObjectTemplate>();

    v8::Isolate* isolate = isolate_->v8();
    PyTypeObject* type = Py_TYPE(instance);

    for (const RegisteredType& entry : registered_types_)
    {
        if (entry.type == type)
            return entry.template_slot->get(isolate)->InstanceTemplate();
    }

    for (PyTypeObject* candidate : unregistered_types_)
    {
        if (candidate == type)
            return v8::Local<v8::ObjectTemplate>();
    }

    // The type's own method resolution order, because a registered base is what a
    // subclass should inherit from.  __mro__ rather than tp_base: the type struct
    // is opaque under the limited API.
    PyRef mro(PyRef::adopt(PyObject_GetAttrString(reinterpret_cast<PyObject*>(type), "__mro__")));
    if (!mro)
    {
        PyErr_Clear();
        unregistered_types_.push_back(type);
        return v8::Local<v8::ObjectTemplate>();
    }

    const Py_ssize_t count = PyTuple_Check(mro.get()) ? PyTuple_Size(mro.get()) : 0;
    v8::Local<v8::ObjectTemplate> found;
    for (Py_ssize_t index = 0; index < count && found.IsEmpty(); ++index)
    {
        PyObject* base = PyTuple_GetItem(mro.get(), index);
        if (base == nullptr)
        {
            PyErr_Clear();
            break;
        }
        for (const RegisteredType& entry : registered_types_)
        {
            if (entry.type == reinterpret_cast<PyTypeObject*>(base))
            {
                found = entry.template_slot->get(isolate)->InstanceTemplate();
                break;
            }
        }
    }

    if (found.IsEmpty())
        unregistered_types_.push_back(type);
    return found;
}

void
Realm::attach(PythonOwner* record)
{
    record->next = owners_;
    record->previous = nullptr;
    if (owners_ != nullptr)
        owners_->previous = record;
    owners_ = record;
    record->realm = this;
}

void
Realm::detach(PythonOwner* record)
{
    if (record->previous != nullptr)
        record->previous->next = record->next;
    else if (owners_ == record)
        owners_ = record->next;
    if (record->next != nullptr)
        record->next->previous = record->previous;

    record->next = nullptr;
    record->previous = nullptr;
    record->realm = nullptr;
}

void
Realm::reap_owners()
{
    PythonOwner* record = owners_;
    while (record != nullptr)
    {
        PythonOwner* next = record->next;
        if (!record->wants_release)
        {
            record = next;
            continue;
        }

        detach(record);
        release_python_owner(*record);
        record = next;
    }
}

PyObject*
Realm::evaluate(const char* source, const char* filename)
{
    if (!require_active("evaluate"))
        return nullptr;

    // terminated describes the last entry, so it is cleared by the next one
    // rather than by a close: a host that recovers should not have to rebuild the
    // realm to stop seeing the previous entry's verdict.
    terminated_ = false;

    IsolateEntry entry(*isolate_);
    if (!entry.ok())
        return nullptr;

    v8::Isolate* isolate = entry.v8();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> local = context();
    if (local.IsEmpty())
    {
        raise(PyExc_RuntimeError, "the realm has no context");
        return nullptr;
    }
    v8::Context::Scope context_scope(local);

    // The budget is armed per entry, not per realm: the watchdog interrupts this
    // entry and stops when it ends, so a script that ran long cannot poison the
    // next one.
    bool watching = false;
    if (timeout_ms_ > 0)
    {
        if (timeout_guard_ == nullptr)
            timeout_guard_.reset(new (std::nothrow) TimeoutGuard());
        if (timeout_guard_ == nullptr)
        {
            PyErr_NoMemory();
            return nullptr;
        }
        watching = timeout_guard_->arm(*isolate_, timeout_ms_);
        if (!watching)
            return nullptr;
    }

    v8::Local<v8::String> code;
    if (!v8::String::NewFromUtf8(isolate, source, v8::NewStringType::kNormal)
             .ToLocal(&code))
    {
        raise(PyExc_RuntimeError, "failed to build the source string");
        if (watching)
            timeout_guard_->disarm();
        return nullptr;
    }

    v8::Local<v8::Value> name = v8::String::Empty(isolate);
    if (filename != nullptr)
    {
        v8::Local<v8::String> file_name;
        if (v8::String::NewFromUtf8(isolate, filename, v8::NewStringType::kNormal)
                .ToLocal(&file_name))
            name = file_name;
    }

    // ScriptOrigin carries a Local<Value>; an empty string is what V8 uses for a
    // script with no name, which is exactly the "javascript" case.
    v8::ScriptOrigin origin(name);
    v8::TryCatch try_catch(isolate);

    v8::Local<v8::Script> script;
    if (!v8::Script::Compile(local, code, &origin).ToLocal(&script))
    {
        if (watching)
            timeout_guard_->disarm();
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *this);
        else
            raise(PyExc_RuntimeError, "failed to compile the script");
        return nullptr;
    }

    v8::Local<v8::Value> result;
    const bool ran = script->Run(local).ToLocal(&result);
    if (watching)
        timeout_guard_->disarm();

    if (!ran)
    {
        if (timeout_guard_ != nullptr && timeout_guard_->fired())
        {
            // Termination is sticky: V8 refuses further work on the isolate until
            // its owner cancels it, which is why this is the one place that does.
            // The realm stays usable afterwards, so a host can recover by
            // raising the budget rather than by building a new realm.
            isolate->CancelTerminateExecution();
            // Recorded on the realm before the guard forgets, so "the last entry
            // ran out of time" survives the cleanup that makes the realm usable
            // again.
            terminated_ = true;
            timeout_guard_->clear_fired();
            raise(PyExc_TimeoutError, "the realm exceeded its %lld millisecond budget",
                  static_cast<long long>(timeout_ms_));
            return nullptr;
        }
        if (try_catch.HasCaught())
            js_exception_to_python(try_catch.Exception(), *this);
        else
            raise(PyExc_RuntimeError, "failed to run the script");
        return nullptr;
    }

    PyObject* converted = to_python_boundary(result, *this);

    // Promise reactions run before the caller sees the result, so a script that
    // resolves a promise does not leave its continuation queued behind its own
    // return.  This is also where an unhandled rejection becomes reportable: V8
    // only decides a rejection is unhandled once the queue has drained.
    if (!run_microtasks() && converted != nullptr)
    {
        Py_DECREF(converted);
        converted = nullptr;
    }

    reap_owners();
    return converted;
}

}  // namespace v8n
