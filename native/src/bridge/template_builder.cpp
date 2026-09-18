#include "bridge/bridge_internal.h"

#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "kernel/v8_compat.h"
#include "v8n/bridge.h"
#include "v8n/realm.h"

#include <cstring>

namespace v8n::bridge
{
namespace
{

v8::Local<v8::String>
utf8(v8::Isolate* isolate, const char* text)
{
    v8::Local<v8::String> result;
    if (text == nullptr)
        return result;
    v8::String::NewFromUtf8(isolate, text, v8::NewStringType::kNormal).ToLocal(&result);
    return result;
}

// Where a member with this location goes.  An interface member is static and
// lives on the function rather than on a template, which is why it is a separate
// answer rather than a third ObjectTemplate.
bool
target_of(v8::Local<v8::FunctionTemplate> template_, std::uint8_t location,
          v8::Local<v8::ObjectTemplate>* target)
{
    switch (location)
    {
        case kLocationInstance:
            *target = template_->InstanceTemplate();
            return true;
        case kLocationPrototype:
            *target = template_->PrototypeTemplate();
            return true;
        default:
            return false;
    }
}

}  // namespace

v8::Local<v8::FunctionTemplate>
build_template(v8::Isolate* isolate, ParsedInterface& interface, Batch& batch,
               v8::Local<v8::FunctionTemplate> parent_template)
{
    const InterfaceSpec& spec = interface.spec;

    // Every interface gets a callback, even one that cannot be constructed: the
    // callback is what refuses, and it is also where an adapter runs, so there is
    // no path that would need a null one.
    v8::Local<v8::Value> data =
        v8c::external_new(isolate, binding_for(batch, interface.constructor_binding));
    v8::Local<v8::FunctionTemplate> template_ = v8::FunctionTemplate::New(
        isolate, interface_construct, data, v8::Local<v8::Signature>(),
        static_cast<int>(spec.length), v8::ConstructorBehavior::kAllow);
    if (template_.IsEmpty())
    {
        raise(PyExc_RuntimeError, "failed to build the template for %s", spec.name);
        return v8::Local<v8::FunctionTemplate>();
    }

    v8::Local<v8::String> name = utf8(isolate, spec.name);
    if (name.IsEmpty())
    {
        raise(PyExc_RuntimeError, "failed to build the name of %s", spec.name);
        return v8::Local<v8::FunctionTemplate>();
    }
    template_->SetClassName(name);

    if (spec.to_string_tag != nullptr)
    {
        v8::Local<v8::String> tag = utf8(isolate, spec.to_string_tag);
        if (!tag.IsEmpty())
        {
            // Read-only and not enumerated, which is what the well-known symbol
            // means: `Object.prototype.toString.call(x)` reads it, `for..in` does
            // not.
            template_->PrototypeTemplate()->Set(
                v8::Symbol::GetToStringTag(isolate), tag,
                static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontEnum));
        }
    }

    if ((spec.traits & kTraitImmutableInstance) != 0)
    {
        // Instances of this interface have an immutable prototype, so a page cannot
        // swap the chain out from under one of them.
        template_->InstanceTemplate()->SetImmutableProto();
    }

    if ((spec.traits & kTraitImmutablePrototype) != 0)
    {
        // And this is the other half, which is a different thing: the prototype
        // object itself is what cannot be changed.  An interface can want either,
        // both, or neither, which is why they are two bits rather than one.
        template_->PrototypeTemplate()->SetImmutableProto();
    }

    if (spec.parent != nullptr && (spec.traits & kTraitSkipInherit) == 0)
    {
        // Resolved by the caller, which is the only layer that knows whether the
        // parent is in this batch or in one already installed.
        if (parent_template.IsEmpty())
        {
            raise(PyExc_RuntimeError, "%s: the parent interface %s was not built", spec.name,
                  spec.parent);
            return v8::Local<v8::FunctionTemplate>();
        }
        template_->Inherit(parent_template);
    }

    for (std::size_t index = 0; index < interface.members.size(); ++index)
    {
        const MemberSpec& member = interface.members[index];
        // A constant has no callback, so it has no binding either; asking for one
        // would raise for a member that is perfectly well described.
        MemberBinding* binding = interface.member_binding[index] == kNoSlot
                                     ? nullptr
                                     : binding_for(batch, interface.member_binding[index]);
        if (binding == nullptr && member.kind != MemberKind::kConstant)
        {
            if (!PyErr_Occurred())
                raise(PyExc_RuntimeError, "%s.%s has no callback binding", spec.name,
                      member.name);
            return v8::Local<v8::FunctionTemplate>();
        }

        // An interface member is static and lives on the function object, which
        // does not exist until this template has been instantiated.  Its turn
        // comes in install_interface_members.
        v8::Local<v8::ObjectTemplate> target;
        if (!target_of(template_, member.location, &target))
            continue;

        v8::Local<v8::Name> member_name = utf8(isolate, member.name);
        if (member_name.IsEmpty())
        {
            raise(PyExc_RuntimeError, "failed to build the name of %s.%s", spec.name, member.name);
            return v8::Local<v8::FunctionTemplate>();
        }

        const auto attribute = static_cast<v8::PropertyAttribute>(member.property_flags);
        const auto side_effect = static_cast<v8::SideEffectType>(member.side_effect);
        const v8::Local<v8::Value> member_data = v8c::external_new(isolate, binding);

        switch (member.kind)
        {
            case MemberKind::kAttribute:
            {
                // Accessor function templates rather than a native data
                // property: the callback has to reach the object the property was
                // read through, and only a function callback offers that.  The
                // side effect goes on the getter, which is where V8 reads it.
                v8::Local<v8::FunctionTemplate> getter = v8::FunctionTemplate::New(
                    isolate, attribute_getter, member_data, v8::Local<v8::Signature>(), 0,
                    v8::ConstructorBehavior::kThrow, side_effect);
                if (getter.IsEmpty())
                {
                    raise(PyExc_RuntimeError, "failed to build the getter for %s.%s", spec.name,
                          member.name);
                    return v8::Local<v8::FunctionTemplate>();
                }

                v8::Local<v8::FunctionTemplate> setter;
                if (binding->setter != nullptr)
                {
                    setter = v8::FunctionTemplate::New(
                        isolate, attribute_setter, member_data, v8::Local<v8::Signature>(), 1,
                        v8::ConstructorBehavior::kThrow, v8::SideEffectType::kHasSideEffect);
                    if (setter.IsEmpty())
                    {
                        raise(PyExc_RuntimeError, "failed to build the setter for %s.%s",
                              spec.name, member.name);
                        return v8::Local<v8::FunctionTemplate>();
                    }
                }
                target->SetAccessorProperty(member_name, getter, setter, attribute);
                break;
            }

            case MemberKind::kMethod:
            {
                v8::Local<v8::FunctionTemplate> method = v8::FunctionTemplate::New(
                    isolate, method_call, member_data, v8::Local<v8::Signature>(),
                    static_cast<int>(member.arity), v8::ConstructorBehavior::kThrow,
                    side_effect);
                if (method.IsEmpty())
                {
                    raise(PyExc_RuntimeError, "failed to build the method %s.%s", spec.name,
                          member.name);
                    return v8::Local<v8::FunctionTemplate>();
                }
                target->Set(member_name, method, attribute);
                break;
            }

            case MemberKind::kConstant:
                target->Set(member_name, v8::Integer::New(isolate, member.constant), attribute);
                break;

            case MemberKind::kConstantCallback:
                // Lazy, so a constant that is expensive to produce costs nothing
                // unless something reads it.  That is the whole difference from
                // kConstant.
                target->SetLazyDataProperty(member_name, lazy_constant_getter, member_data,
                                            attribute, side_effect);
                break;
        }
    }

    if ((spec.traits & kTraitUndetectable) != 0)
    {
        // `typeof` reports undefined and `== null` is true, which is what the one
        // object JavaScript has that behaves this way is.  On the instance
        // template, because instances are what JavaScript is handed.
        template_->InstanceTemplate()->MarkAsUndetectable();
    }

    if (interface.call_binding != kNoSlot)
    {
        // The instance is callable.  The binding travels as the callback's data,
        // the same way a method's does, so the handler finds its Python callable by
        // dereferencing a pointer rather than by looking anything up.
        MemberBinding* binding = binding_for(batch, interface.call_binding);
        if (binding == nullptr)
        {
            if (!PyErr_Occurred())
                raise(PyExc_RuntimeError, "%s has no call binding", spec.name);
            return v8::Local<v8::FunctionTemplate>();
        }
        template_->InstanceTemplate()->SetCallAsFunctionHandler(
            instance_call, v8c::external_new(isolate, binding));
    }

    if (spec.array_flags != 0)
    {
        // The engine's own collection methods, borrowed rather than reimplemented.
        // Each intrinsic reads `length` and the indexed handler, both of which the
        // host already answers for the object; writing them out here would be
        // writing the engine's semantics a second time, and getting them subtly
        // wrong the second time.
        v8::Local<v8::ObjectTemplate> prototype = template_->PrototypeTemplate();
        if ((spec.array_flags & kArrayIterator) != 0)
            prototype->SetIntrinsicDataProperty(v8::Symbol::GetIterator(isolate),
                                                v8::kArrayProto_values, v8::DontEnum);
        if ((spec.array_flags & kArrayEntries) != 0)
            prototype->SetIntrinsicDataProperty(
                v8::String::NewFromUtf8Literal(isolate, "entries"), v8::kArrayProto_entries,
                v8::None);
        if ((spec.array_flags & kArrayKeys) != 0)
            prototype->SetIntrinsicDataProperty(
                v8::String::NewFromUtf8Literal(isolate, "keys"), v8::kArrayProto_keys,
                v8::None);
        if ((spec.array_flags & kArrayValues) != 0)
            prototype->SetIntrinsicDataProperty(
                v8::String::NewFromUtf8Literal(isolate, "values"), v8::kArrayProto_values,
                v8::None);
        if ((spec.array_flags & kArrayForEach) != 0)
            prototype->SetIntrinsicDataProperty(
                v8::String::NewFromUtf8Literal(isolate, "forEach"), v8::kArrayProto_forEach,
                v8::None);
    }

    if (!install_handlers(isolate, interface, batch, template_))
        return v8::Local<v8::FunctionTemplate>();

    return template_;
}

namespace
{

// Builds a configuration from one table of slots.
//
// kNonMasking is what makes "not mine" mean something: the object answers for the
// names its host owns and lets everything else resolve through the prototype
// chain.  A masking interceptor would claim every name instead, and would do it
// to every realm at once.
v8::NamedPropertyHandlerConfiguration
named_configuration(const std::uint32_t* slots, v8::Local<v8::Value> data)
{
    return v8::NamedPropertyHandlerConfiguration(
        slots[kHandlerNameGet] != kNoSlot ? named_getter : nullptr,
        slots[kHandlerNameSet] != kNoSlot ? named_setter : nullptr,
        slots[kHandlerNameQuery] != kNoSlot ? named_query : nullptr,
        slots[kHandlerNameDel] != kNoSlot ? named_deleter : nullptr,
        slots[kHandlerNameEnum] != kNoSlot ? named_enumerator : nullptr, data,
        v8::PropertyHandlerFlags::kNonMasking);
}

v8::IndexedPropertyHandlerConfiguration
indexed_configuration(const std::uint32_t* slots, v8::Local<v8::Value> data)
{
    return v8::IndexedPropertyHandlerConfiguration(
        slots[kHandlerIndexGet] != kNoSlot ? indexed_getter : nullptr,
        slots[kHandlerIndexSet] != kNoSlot ? indexed_setter : nullptr,
        slots[kHandlerIndexQuery] != kNoSlot ? indexed_query : nullptr,
        slots[kHandlerIndexDel] != kNoSlot ? indexed_deleter : nullptr,
        slots[kHandlerIndexEnum] != kNoSlot ? indexed_enumerator : nullptr, data,
        v8::PropertyHandlerFlags::kNonMasking);
}

// Fills one table's bindings and says which families it covers.  Collected before
// anything is installed, because a configuration carries one data for all of its
// callbacks and the table has to be complete by then.
bool
collect_handlers(Batch& batch, ParsedInterface& interface, const std::uint32_t* slots,
                 const std::uint32_t* bindings, HandlerData& data, bool* any_named,
                 bool* any_indexed)
{
    data.realm = batch.realm;
    *any_named = false;
    *any_indexed = false;
    for (std::uint32_t role = 0; role < kHandlerCount; ++role)
    {
        if (slots[role] == kNoSlot)
            continue;

        MemberBinding* binding = binding_for(batch, bindings[role]);
        if (binding == nullptr)
        {
            if (!PyErr_Occurred())
                raise(PyExc_RuntimeError, "%s: handler %s has no binding", interface.spec.name,
                      handler_role_name(static_cast<HandlerRole>(role)));
            return false;
        }
        data.roles[role] = binding;

        if (role <= kHandlerNameEnum)
            *any_named = true;
        else
            *any_indexed = true;
    }
    return true;
}

}  // namespace

bool
install_handlers(v8::Isolate* isolate, ParsedInterface& interface, Batch& batch,
                 v8::Local<v8::FunctionTemplate> interface_template)
{
    const InterfaceSpec& spec = interface.spec;
    v8::Local<v8::ObjectTemplate> instance = interface_template->InstanceTemplate();

    if (spec.handlers != nullptr)
    {
        bool any_named = false;
        bool any_indexed = false;
        if (!collect_handlers(batch, interface, spec.handlers, interface.handler_binding,
                              interface.handler_data, &any_named, &any_indexed))
            return false;

        if (any_named || any_indexed)
        {
            // The table is the data, not the bindings: V8 hands the same data to
            // every callback in a configuration.
            v8::Local<v8::Value> data = v8c::external_new(isolate, &interface.handler_data);
            if (any_named)
                instance->SetHandler(named_configuration(spec.handlers, data));
            if (any_indexed)
                instance->SetHandler(indexed_configuration(spec.handlers, data));
        }
    }

    if (spec.cross_origin_check == kNoSlot)
        return true;

    // The policy belongs to the object's own realm, so the check and the answers it
    // guards are installed on the object's own template and consulted by V8 before
    // another realm is let in.
    MemberBinding* check = binding_for(batch, interface.cross_origin_check_binding);
    if (check == nullptr)
    {
        if (!PyErr_Occurred())
            raise(PyExc_RuntimeError, "%s: the cross-origin check has no binding", spec.name);
        return false;
    }
    v8::Local<v8::Value> check_data = v8c::external_new(isolate, check);

    if (spec.cross_origin_handlers == nullptr)
    {
        instance->SetAccessCheckCallback(cross_origin_access_check, check_data);
        return true;
    }

    // A second table, and a second configuration carrying it, so the same callback
    // functions answer a stranger from the cross-origin bindings.  Reusing the
    // same-origin bindings here would leak exactly what the policy exists to
    // protect.
    bool any_named = false;
    bool any_indexed = false;
    if (!collect_handlers(batch, interface, spec.cross_origin_handlers,
                          interface.cross_origin_binding, interface.cross_origin_data,
                          &any_named, &any_indexed))
        return false;

    v8::Local<v8::Value> cross_data = v8c::external_new(isolate, &interface.cross_origin_data);
    instance->SetAccessCheckCallbackAndHandler(
        cross_origin_access_check,
        named_configuration(spec.cross_origin_handlers, cross_data),
        indexed_configuration(spec.cross_origin_handlers, cross_data), check_data);
    return true;
}

bool
install_interface_members(v8::Isolate* isolate, v8::Local<v8::Context> context,
                          v8::Local<v8::Function> function, ParsedInterface& interface,
                          Batch& batch)
{
    const InterfaceSpec& spec = interface.spec;

    for (std::size_t index = 0; index < interface.members.size(); ++index)
    {
        const MemberSpec& member = interface.members[index];
        if (member.location != kLocationInterface)
            continue;

        MemberBinding* binding = interface.member_binding[index] == kNoSlot
                                     ? nullptr
                                     : binding_for(batch, interface.member_binding[index]);
        if (binding == nullptr && member.kind != MemberKind::kConstant)
        {
            if (!PyErr_Occurred())
                raise(PyExc_RuntimeError, "%s.%s has no callback binding", spec.name,
                      member.name);
            return false;
        }

        v8::Local<v8::Name> member_name = utf8(isolate, member.name);
        if (member_name.IsEmpty())
        {
            raise(PyExc_RuntimeError, "failed to build the name of %s.%s", spec.name, member.name);
            return false;
        }

        const auto attribute = static_cast<v8::PropertyAttribute>(member.property_flags);
        const auto side_effect = static_cast<v8::SideEffectType>(member.side_effect);
        const v8::Local<v8::Value> member_data = v8c::external_new(isolate, binding);
        bool ok = true;

        switch (member.kind)
        {
            case MemberKind::kAttribute:
                // The native data property form, which is fine here precisely
                // because a static attribute has no receiver to reach.
                ok = function
                         ->SetNativeDataProperty(
                             context, member_name, static_attribute_getter,
                             binding->setter == nullptr ? nullptr : static_attribute_setter,
                             member_data, attribute, side_effect,
                             v8::SideEffectType::kHasSideEffect)
                         .FromMaybe(false);
                break;

            case MemberKind::kMethod:
            {
                v8::Local<v8::FunctionTemplate> method = v8::FunctionTemplate::New(
                    isolate, method_call, member_data, v8::Local<v8::Signature>(),
                    static_cast<int>(member.arity), v8::ConstructorBehavior::kThrow,
                    side_effect);
                v8::Local<v8::Function> callable;
                ok = !method.IsEmpty() && method->GetFunction(context).ToLocal(&callable) &&
                     function->Set(context, member_name, callable).FromMaybe(false);
                break;
            }

            case MemberKind::kConstant:
                ok = function
                         ->Set(context, member_name, v8::Integer::New(isolate, member.constant))
                         .FromMaybe(false);
                break;

            case MemberKind::kConstantCallback:
                ok = function
                         ->SetLazyDataProperty(context, member_name, lazy_constant_getter,
                                               member_data, attribute, side_effect)
                         .FromMaybe(false);
                break;
        }

        if (!ok)
        {
            raise(PyExc_RuntimeError, "failed to install the static member %s.%s", spec.name,
                  member.name);
            return false;
        }
    }
    return true;
}

}  // namespace v8n::bridge
