// Installing a batch.
//
// Split into two phases, because one of them has to happen before the context
// exists.  Building templates needs only the isolate, and a realm that is going to
// have a Python global object needs the global interface's *template* at
// Context::New -- V8 takes the global template there and offers no way to give the
// global object an interceptor afterwards.  Everything context-dependent -- the
// functions, the static members, the names -- comes after.

#include "bridge/bridge_internal.h"

#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "kernel/v8_compat.h"
#include "v8n/boundary.h"
#include "v8n/bridge.h"
#include "v8n/realm.h"

#include <cstring>
#include <new>

namespace v8n::bridge
{

ParsedInterface*
Batch::global_interface() const
{
    for (const std::unique_ptr<ParsedInterface>& interface : interfaces)
    {
        if ((interface->spec.traits & kTraitGlobalThis) != 0)
            return interface.get();
    }
    return nullptr;
}

ParsedInterface*
Batch::global_interface(PyObject* host) const
{
    if (host == nullptr)
        return global_interface();

    // The host object's own type decides which global interface it is.  A batch
    // can carry more than one -- a window's description mentions Window, and the
    // interfaces a window exposes include the ones a worker scope is built from
    // -- and binding the global to the first one would make every inherited
    // member resolve against the wrong class.
    PyTypeObject* kind = Py_TYPE(host);
    ParsedInterface* first = nullptr;
    ParsedInterface* best = nullptr;
    for (const std::unique_ptr<ParsedInterface>& interface : interfaces)
    {
        if ((interface->spec.traits & kTraitGlobalThis) == 0)
            continue;
        if (first == nullptr)
            first = interface.get();
        if (interface->type_object == nullptr
            || !PyType_IsSubtype(kind, interface->type_object))
            continue;
        // The most derived match wins.  A host object is an instance of every
        // class in its chain, and two global interfaces of one description are
        // usually a base and the scope that extends it; the one the object most
        // *is* is the interface its members were declared on.
        if (best == nullptr || PyType_IsSubtype(interface->type_object, best->type_object))
            best = interface.get();
    }
    // Nothing matched, so the host is not one of the batch's own types: its order
    // is the only thing left to go on, and saying so beats refusing to create the
    // realm at all.
    return best != nullptr ? best : first;
}

bool
build_templates(Isolate& isolate, Batch& batch, PyObject* global_this)
{
    v8::Isolate* v8 = isolate.v8();

    // Parents before children.  A description is written in whatever order reads
    // best, so the order it arrives in cannot be trusted and a parent may well be
    // declared after its child.  Repeated passes build whatever is currently
    // possible; a pass that builds nothing means the rest have a parent that is
    // missing or part of a cycle.
    std::size_t built = 0;
    const std::size_t total = batch.interfaces.size();
    while (built < total)
    {
        bool progress = false;
        for (std::unique_ptr<ParsedInterface>& interface : batch.interfaces)
        {
            if (interface->template_slot != nullptr)
                continue;

            v8::Local<v8::FunctionTemplate> parent_template;
            if (interface->spec.parent != nullptr &&
                (interface->spec.traits & kTraitSkipInherit) == 0)
            {
                ParsedInterface* parent = batch.find(interface->spec.parent);
                if (parent != nullptr)
                {
                    if (parent->template_slot == nullptr)
                        continue;
                    parent_template = parent->template_slot->get(v8);
                }
                else
                {
                    // Not in this batch, so it has to be one an earlier batch
                    // installed: a host that builds interfaces on demand installs
                    // one at a time, and the child of a later batch inherits from
                    // the parent a previous one left behind.
                    parent_template =
                        batch.realm->installed_interface(interface->spec.parent);
                    if (parent_template.IsEmpty())
                    {
                        raise(PyExc_ValueError,
                              "%s: no interface named %s in this batch or in this realm",
                              interface->spec.name, interface->spec.parent);
                        return false;
                    }
                }
            }

            v8::Local<v8::FunctionTemplate> template_ =
                build_template(v8, *interface, batch, parent_template);
            if (template_.IsEmpty())
                return false;

            auto* slot = new (std::nothrow) HandleSlot<v8::FunctionTemplate>();
            if (slot == nullptr)
            {
                PyErr_NoMemory();
                return false;
            }
            slot->emplace(isolate, template_);
            interface->template_slot = slot;
            ++built;
            progress = true;
        }

        if (!progress)
        {
            raise(PyExc_ValueError,
                  "the batch's interfaces form a cycle in their parent chain, so none of "
                  "them can be built first");
            return false;
        }
    }

    // Lazy names become lazy properties on the global object, which is where a
    // host's unbuilt interfaces are exposed -- and the global object's template is
    // built here, before the context exists, which is the only window in which a
    // property can be added to it at all.
    if (!batch.lazy.empty())
    {
        ParsedInterface* global = batch.global_interface(global_this);
        if (global == nullptr)
        {
            raise(PyExc_ValueError,
                  "a lazy name is exposed on the global object, so the batch needs an "
                  "interface marked global_this to expose it on");
            return false;
        }

        v8::Local<v8::ObjectTemplate> instance =
            global->template_slot->get(v8)->InstanceTemplate();
        for (const std::unique_ptr<LazyBinding>& binding : batch.lazy)
        {
            binding->factory = batch.lazy_factory;

            v8::Local<v8::String> lazy_name;
            if (!v8::String::NewFromUtf8(v8, binding->name.c_str(), v8::NewStringType::kNormal)
                     .ToLocal(&lazy_name))
            {
                raise(PyExc_RuntimeError, "failed to build the name of the lazy interface %s",
                      binding->name.c_str());
                return false;
            }

            // DontEnum, because a lazy name that turned up in a for-in loop would
            // make enumerating the global object build every interface it enumerated,
            // which is the cost this exists to avoid.
            instance->SetLazyDataProperty(lazy_name, lazy_interface_getter,
                                          v8c::external_new(v8, binding.get()), v8::DontEnum);
        }
    }
    return true;
}

bool
finish_batch(Realm& realm, v8::Local<v8::Context> context, Batch& batch,
             const char* deferred_name, v8::Local<v8::Value>* deferred_value)
{
    v8::Isolate* isolate = realm.isolate().v8();
    v8::TryCatch try_catch(isolate);

    for (std::unique_ptr<ParsedInterface>& interface : batch.interfaces)
    {
        v8::Local<v8::Function> function;
        if (!interface->template_slot->get(isolate)->GetFunction(context).ToLocal(&function))
        {
            if (try_catch.HasCaught())
                js_exception_to_python(try_catch.Exception(), realm);
            else
                raise(PyExc_RuntimeError, "failed to instantiate %s", interface->spec.name);
            return false;
        }

        v8::Local<v8::String> name;
        if (!v8::String::NewFromUtf8(isolate, interface->spec.name, v8::NewStringType::kNormal)
                 .ToLocal(&name))
        {
            raise(PyExc_RuntimeError, "failed to build the name of %s", interface->spec.name);
            return false;
        }

        // The constructor's own name, so a stack trace says `new Event()` rather
        // than `new anonymous`.
        function->SetName(name);

        // Interface members need the function object, so they are installed here
        // rather than while the template was being built.
        if (!install_interface_members(isolate, context, function, *interface, batch))
            return false;

        if (deferred_name != nullptr && std::strcmp(interface->spec.name, deferred_name) == 0)
        {
            // The caller is the getter for this very name, and V8 is in the middle of
            // initializing that property.  Writing it here as well would be writing it
            // twice -- and the property's lookup state is still the lazy one, which
            // the write path checks against and does not expect.  V8 caches whatever
            // the getter returns, so the value goes back to the getter.
            if (deferred_value != nullptr)
                *deferred_value = function;
        }
        // DontEnum, so that enumerating the global object reports the names a
        // page put there rather than the several hundred interfaces it was
        // given.  The host declares which names it exposes; which of them a
        // page has read is not what `for (name in window)` is asking about.
        else if (interface->spec.exposed)
        {
            if (!context->Global()
                     ->DefineOwnProperty(context, name, function, v8::DontEnum)
                     .FromMaybe(false))
            {
                if (try_catch.HasCaught())
                    js_exception_to_python(try_catch.Exception(), realm);
                else
                    raise(PyExc_RuntimeError, "failed to expose %s on the global object",
                          interface->spec.name);
                return false;
            }
        }

        // An interface that names a Python type is registered with the realm, which
        // keeps its own handle: that is how an instance built in Python finds this
        // interface's prototype.  The instance template cannot be recovered from
        // the function, so somebody has to hold the template.
        if (interface->type_object != nullptr &&
            !realm.register_python_type(interface->type_object,
                                        interface->template_slot->get(isolate)))
            return false;

        // Registered with the realm before the batch lets its own handle go: a
        // later batch's child inherits from this template, and the constructor
        // function V8 produced cannot be turned back into one.
        if (!realm.register_interface_template(interface->spec.name,
                                               interface->template_slot->get(isolate)))
            return false;

        // The function keeps the template alive for JavaScript, and the realm keeps
        // it too when the interface names a Python type.  Either way the batch
        // holds no handle of its own beyond the session.
        interface->template_slot->reset();
        delete interface->template_slot;
        interface->template_slot = nullptr;
    }
    return true;
}

bool
install_description(Realm& realm, PyObject* description, const char* deferred_name,
                    v8::Local<v8::Value>* deferred_value)
{
    if (!realm.require_active("install_interfaces"))
        return false;

    auto batch = std::unique_ptr<Batch>(new (std::nothrow) Batch());
    if (batch == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }

    // Reading the whole description first is what makes the failure mode "nothing
    // happened" rather than "half a realm".
    if (!parse_batch(realm, description, batch.get()))
        return false;

    PyEntry entry(realm);
    if (!entry.ok())
        return false;

    if (!build_templates(realm.isolate(), *batch.get()))
        return false;
    if (!finish_batch(realm, entry.context(), *batch.get(), deferred_name, deferred_value))
        return false;

    // The batch outlives this call: every callback V8 hands back carries a
    // pointer into it, and the realm releases it at close.  Owning it is what
    // keeps the callables alive for exactly as long as dispatch is possible.
    realm.own_state(batch.release(), release_batch);
    return true;
}

}  // namespace v8n::bridge

namespace v8n
{

bool
install_batch(Realm& realm, PyObject* description)
{
    return bridge::install_description(realm, description);
}

}  // namespace v8n
