// Reading a batch description.
//
// A description is written by hand -- pyv8c compiles a hundred or so interfaces
// into one -- so the only useful thing to say about a bad one is which interface
// and which field.  Every failure here names both, and the whole description is
// read before anything is installed, so a typo cannot leave a realm half built.
//
// Enumerated fields are strings ("instance", "receiver_check") and numeric fields
// are numbers (property flags, side effects, arity).  A number that V8 uses
// directly is passed through; a number that selects one of a handful of
// behaviours is spelled out, because a bare 1 in a description says nothing about
// what it means.

#include "bridge/bridge_internal.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/handle_slot.h"
#include "v8n/bridge.h"
#include "v8n/realm.h"

#include <cstring>
#include <new>

namespace v8n::bridge
{
namespace
{

struct NameValue
{
    const char* name;
    unsigned value;
};

constexpr NameValue kKindNames[] = {
    {"attribute", static_cast<unsigned>(MemberKind::kAttribute)},
    {"method", static_cast<unsigned>(MemberKind::kMethod)},
    {"constant", static_cast<unsigned>(MemberKind::kConstant)},
    {"constant_callback", static_cast<unsigned>(MemberKind::kConstantCallback)},
};

constexpr NameValue kLocationNames[] = {
    {"instance", kLocationInstance},
    {"prototype", kLocationPrototype},
    {"interface", kLocationInterface},
};

constexpr NameValue kMemberFlagNames[] = {
    {"receiver_check", kMemberReceiverCheck},
    {"has_setter", kMemberHasSetter},
    {"cross_origin_get", kMemberCrossOriginGetCheck},
    {"cross_origin_set", kMemberCrossOriginSetCheck},
    {"replaceable", kMemberReplaceable},
    {"cross_origin", kMemberCrossOriginCheck},
};

constexpr NameValue kTraitNames[] = {
    {"global_this", kTraitGlobalThis},
    {"undetectable", kTraitUndetectable},
    {"unconstructable", kTraitUnconstructable},
    {"as_object", kTraitAsObject},
    {"skip_inherit", kTraitSkipInherit},
    {"has_constructor", kTraitHasConstructor},
    {"immutable_instance", kTraitImmutableInstance},
    {"immutable_prototype", kTraitImmutablePrototype},
};

constexpr NameValue kConstructNames[] = {
    {"new", kConstructNew},
    {"call", kConstructCall},
};

constexpr NameValue kHandlerNames[] = {
    {"name_get", kHandlerNameGet},
    {"name_set", kHandlerNameSet},
    {"name_query", kHandlerNameQuery},
    {"name_del", kHandlerNameDel},
    {"name_enum", kHandlerNameEnum},
    {"index_get", kHandlerIndexGet},
    {"index_set", kHandlerIndexSet},
    {"index_query", kHandlerIndexQuery},
    {"index_del", kHandlerIndexDel},
    {"index_enum", kHandlerIndexEnum},
};



PyObject*
field(PyObject* mapping, const char* key)
{
    return PyDict_GetItemString(mapping, key);
}

// Reads a string field.  Returns false with *present false when the field is
// absent, which is how every optional field is expressed; the caller decides
// whether that is allowed.
bool
string_field(PyObject* mapping, const char* key, std::string* out, bool* present)
{
    *present = false;
    PyObject* value = field(mapping, key);
    if (value == nullptr || value == Py_None)
        return true;

    PyObject* text = value;
    if (!PyUnicode_Check(text))
    {
        raise(PyExc_TypeError, "%s must be a string", key);
        return false;
    }
    const char* rendered = abi3::Utf8(text);
    if (rendered == nullptr)
        return false;
    *out = rendered;
    *present = true;
    return true;
}

bool
integer_field(PyObject* mapping, const char* key, long long fallback, long long* out)
{
    PyObject* value = field(mapping, key);
    if (value == nullptr || value == Py_None)
    {
        *out = fallback;
        return true;
    }
    const long long parsed = PyLong_AsLongLong(value);
    if (parsed == -1 && PyErr_Occurred())
        return false;
    *out = parsed;
    return true;
}

// 0xFFFFFFFF is the "no callback" sentinel, so it is not a slot.
bool
slot_field(PyObject* mapping, const char* key, std::uint32_t* out)
{
    long long parsed = 0;
    if (!integer_field(mapping, key, -1, &parsed))
        return false;
    if (parsed < 0 || parsed >= static_cast<long long>(kNoSlot))
    {
        *out = kNoSlot;
        return true;
    }
    *out = static_cast<std::uint32_t>(parsed);
    return true;
}

bool
lookup(const NameValue* table, std::size_t size, const char* text, unsigned* out)
{
    for (std::size_t index = 0; index < size; ++index)
    {
        if (std::strcmp(text, table[index].name) == 0)
        {
            *out = table[index].value;
            return true;
        }
    }
    return false;
}

// A sequence of names, combined into one word.
bool
flag_field(PyObject* mapping, const char* key, const NameValue* table, std::size_t size,
           unsigned fallback, unsigned* out)
{
    PyObject* value = field(mapping, key);
    if (value == nullptr || value == Py_None)
    {
        *out = fallback;
        return true;
    }

    PyRef sequence(PyRef::adopt(PySequence_Fast(value, "flags must be a sequence of names")));
    if (!sequence)
        return false;

    unsigned combined = 0;
    const Py_ssize_t count = PySequence_Size(sequence.get());
    if (count < 0)
        return false;
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(sequence.get(), index)));
        if (!item)
            return false;
        if (!PyUnicode_Check(item.get()))
        {
            raise(PyExc_TypeError, "%s entries must be strings", key);
            return false;
        }
        const char* text = abi3::Utf8(item.get());
        if (text == nullptr)
            return false;

        unsigned bit = 0;
        if (!lookup(table, size, text, &bit))
        {
            raise(PyExc_ValueError, "unknown %s entry: %s", key, text);
            return false;
        }
        combined |= bit;
    }

    *out = combined;
    return true;
}

// One name that selects a behaviour, validated against the table.
bool
name_field(PyObject* mapping, const char* key, const NameValue* table, std::size_t size,
           unsigned fallback, unsigned* out)
{
    PyObject* value = field(mapping, key);
    if (value == nullptr || value == Py_None)
    {
        *out = fallback;
        return true;
    }
    if (!PyUnicode_Check(value))
    {
        raise(PyExc_TypeError, "%s must be a string", key);
        return false;
    }
    const char* text = abi3::Utf8(value);
    if (text == nullptr)
        return false;

    if (!lookup(table, size, text, out))
    {
        raise(PyExc_ValueError, "unknown %s: %s", key, text);
        return false;
    }
    return true;
}

// Registers a callable as a member's callback target.
bool
resolve_slot(Batch* batch, const char* interface_name, std::uint32_t slot, PyObject** out)
{
    PyObject* callable = PyTuple_GetItem(batch->implementations, static_cast<Py_ssize_t>(slot));
    if (callable == nullptr)
    {
        // PyTuple_GetItem reports an out-of-range index as an IndexError, which
        // says nothing about which interface asked for it.
        PyErr_Clear();
        raise(PyExc_TypeError, "%s: implementations has no entry %u", interface_name, slot);
        return false;
    }
    if (!PyCallable_Check(callable))
    {
        raise(PyExc_TypeError, "%s: implementations[%u] is not callable", interface_name, slot);
        return false;
    }
    *out = callable;
    return true;
}

// force creates a binding even when there is no callback, which is what a
// constructor needs: the binding is how the callback finds its interface, and
// "this cannot be constructed" has to be answerable.
bool
add_binding(Batch* batch, ParsedInterface* interface, std::uint32_t slot,
            std::uint32_t setter_slot, const char* member_name, const MemberSpec* spec,
            const InterfaceSpec* interface_spec, std::uint32_t* out, bool force = false)
{
    *out = kNoSlot;
    if (!force && slot == kNoSlot && setter_slot == kNoSlot)
        return true;

    auto binding = std::unique_ptr<MemberBinding>(new (std::nothrow) MemberBinding());
    if (binding == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }
    binding->realm = batch->realm;
    binding->member_name = member_name;
    binding->interface_spec = interface_spec;
    binding->spec = spec;

    if (slot != kNoSlot && !resolve_slot(batch, interface->spec.name, slot, &binding->callable))
        return false;
    if (setter_slot != kNoSlot &&
        !resolve_slot(batch, interface->spec.name, setter_slot, &binding->setter))
        return false;

    *out = static_cast<std::uint32_t>(batch->bindings.size());
    batch->bindings.push_back(std::move(binding));
    return true;
}

// Reads the handlers a host declared for each intercepted operation it answers.
//
// A handler is a member in every respect except that it has no name: the host
// answers per name at run time, which is the whole point of having one.
//
// The two tables -- same-origin and cross-origin -- go through the same reader
// because a role means the same thing in both; only which table it lands in
// differs.  "check" is the one key that is not a role, and the caller reads it.
bool
parse_handlers(PyObject* mapping, Batch* batch, ParsedInterface* interface,
               std::uint32_t* slots, std::uint32_t* bindings, bool cross_origin)
{
    PyObject* key = nullptr;
    PyObject* value = nullptr;
    Py_ssize_t position = 0;
    while (PyDict_Next(mapping, &position, &key, &value))
    {
        const char* text = abi3::Utf8(key);
        if (text == nullptr)
            return false;

        if (std::strcmp(text, "check") == 0)
            continue;

        unsigned role = 0;
        if (!lookup(kHandlerNames, std::size(kHandlerNames), text, &role))
        {
            raise(PyExc_ValueError, "%s: unknown %s handler %s", interface->spec.name,
                  cross_origin ? "cross-origin" : "property", text);
            return false;
        }

        const long long slot = PyLong_AsLongLong(value);
        if (slot == -1 && PyErr_Occurred())
            return false;
        if (slot < 0 || slot >= static_cast<long long>(kNoSlot))
        {
            raise(PyExc_ValueError, "%s: handler %s needs a slot", interface->spec.name, text);
            return false;
        }

        // The role name is what a trace event and an error message call this
        // handler, so it is copied rather than borrowed from the key object.
        interface->strings.emplace_back(text);
        const char* handler_name = interface->strings.back().c_str();

        slots[role] = static_cast<std::uint32_t>(slot);
        if (!add_binding(batch, interface, static_cast<std::uint32_t>(slot), kNoSlot, handler_name,
                         nullptr, nullptr, &bindings[role]))
            return false;
    }
    return true;
}

// The interface's cross-origin policy: what decides whether a stranger may touch
// its objects, and what answers a stranger when the answer is yes.
bool
parse_cross_origin(PyObject* mapping, Batch* batch, ParsedInterface* interface)
{
    PyObject* check = field(mapping, "check");
    if (check != nullptr && check != Py_None)
    {
        const long long slot = PyLong_AsLongLong(check);
        if (slot == -1 && PyErr_Occurred())
            return false;
        if (slot < 0 || slot >= static_cast<long long>(kNoSlot))
        {
            raise(PyExc_ValueError, "%s: the cross-origin check needs a slot",
                  interface->spec.name);
            return false;
        }

        interface->strings.emplace_back("cross_origin_check");
        interface->spec.cross_origin_check = static_cast<std::uint32_t>(slot);
        if (!add_binding(batch, interface, static_cast<std::uint32_t>(slot), kNoSlot,
                         interface->strings.back().c_str(), nullptr, nullptr,
                         &interface->cross_origin_check_binding))
            return false;
    }

    if (!parse_handlers(mapping, batch, interface, interface->cross_origin_slots,
                        interface->cross_origin_binding, /*cross_origin=*/true))
        return false;

    bool any = false;
    for (std::uint32_t role = 0; role < kHandlerCount; ++role)
    {
        if (interface->cross_origin_slots[role] != kNoSlot)
        {
            any = true;
            break;
        }
    }

    // Handlers nobody can reach: they are consulted only after a check has denied,
    // so without a check they are a description that means nothing.  Refusing beats
    // installing an interface whose cross-origin answers silently never happen.
    if (any && interface->spec.cross_origin_check == kNoSlot)
        return schema_error(interface->spec.name, "cross_origin",
                            "cross-origin handlers need a check, or nothing would ask "
                            "for them");

    if (any)
        interface->spec.cross_origin_handlers = interface->cross_origin_slots;
    return true;
}

bool
parse_member(PyObject* mapping, Batch* batch, ParsedInterface* interface, MemberSpec* spec,
             std::uint32_t* binding_index)
{
    const char* interface_name = interface->spec.name;

    PyObject* name_object = field(mapping, "name");
    if (name_object == nullptr || name_object == Py_None)
        return schema_error(interface_name, "name", "every member needs a name");
    const char* name = abi3::Utf8(name_object);
    if (name == nullptr)
        return false;
    interface->strings.emplace_back(name);
    spec->name = interface->strings.back().c_str();

    unsigned kind = 0;
    if (!name_field(mapping, "kind", kKindNames, std::size(kKindNames),
                    static_cast<unsigned>(MemberKind::kMethod), &kind))
        return false;
    spec->kind = static_cast<MemberKind>(kind);

    // The default is the prototype, which is where an interface member belongs:
    // an instance location allocates a property on every object, and an interface
    // location means "static" and has to be asked for.
    unsigned location = kLocationPrototype;
    if (!name_field(mapping, "location", kLocationNames, std::size(kLocationNames),
                    kLocationPrototype, &location))
        return false;
    spec->location = static_cast<std::uint8_t>(location);

    unsigned flags = 0;
    if (!flag_field(mapping, "flags", kMemberFlagNames, std::size(kMemberFlagNames), 0, &flags))
        return false;

    // Same rule as the interface traits: refused, not ignored.  The cross-origin
    // flags are accepted rather than refused because they name the interface's
    // policy for this member, and that policy is installed on the interface --
    // V8 enforces an access check per template, not per member, so there is
    // nothing finer to do here and refusing them would reject a declaration whose
    // meaning is already honoured.
    constexpr unsigned kAcceptedFlags = kMemberReceiverCheck | kMemberHasSetter |
                                        kMemberCrossOriginGetCheck |
                                        kMemberCrossOriginSetCheck | kMemberReplaceable |
                                        kMemberCrossOriginCheck;
    if ((flags & ~kAcceptedFlags) != 0)
        return schema_error(interface_name, "flags", "this flag is not enforced yet");
    spec->flags = static_cast<std::uint8_t>(flags);

    long long numbers = 0;
    if (!integer_field(mapping, "property_flags", 0, &numbers))
        return false;
    if (numbers < 0 || numbers > 7)
        return schema_error(interface_name, "property_flags",
                            "property flags are the three V8 attribute bits, 0 to 7");
    spec->property_flags = static_cast<std::uint8_t>(numbers);

    if (!integer_field(mapping, "side_effect", 0, &numbers))
        return false;
    if (numbers < 0 || numbers > 2)
        return schema_error(interface_name, "side_effect", "0, 1 or 2");
    spec->side_effect = static_cast<std::uint8_t>(numbers);

    if (!integer_field(mapping, "arity", 0, &numbers))
        return false;
    if (numbers < 0 || numbers > 255)
        return schema_error(interface_name, "arity", "0 to 255");
    spec->arity = static_cast<std::uint8_t>(numbers);

    spec->constant = 0;
    if (spec->kind == MemberKind::kConstant)
    {
        if (!integer_field(mapping, "value", 0, &numbers))
            return false;
        if (numbers < INT32_MIN || numbers > INT32_MAX)
            return schema_error(interface_name, "value",
                                "a constant has to fit a V8 32-bit integer");
        spec->constant = static_cast<std::int32_t>(numbers);
    }

    if (!slot_field(mapping, "slot", &spec->slot))
        return false;
    if (!slot_field(mapping, "setter_slot", &spec->setter_slot))
        return false;

    // A constant has no callable, and saying otherwise is a mistake worth
    // catching rather than silently dropping.
    const bool needs_slot = spec->kind != MemberKind::kConstant;
    if (!needs_slot && spec->slot != kNoSlot)
        return schema_error(interface_name, "slot", "a constant has no callback");
    if (needs_slot && spec->slot == kNoSlot)
        return schema_error(interface_name, "slot", "this member needs a callback");

    if (spec->setter_slot != kNoSlot && spec->kind != MemberKind::kAttribute)
        return schema_error(interface_name, "setter_slot", "only an attribute has a setter");
    // A setter slot is the flag's other half: one without the other is a
    // description that means two different things depending on who reads it.
    const bool flagged = (spec->flags & kMemberHasSetter) != 0;
    if (flagged != (spec->setter_slot != kNoSlot))
        return schema_error(interface_name, "setter_slot",
                            "a setter must have both a slot and the has_setter flag");

    return add_binding(batch, interface, spec->slot, spec->setter_slot, spec->name, spec,
                       nullptr, binding_index);
}

bool
parse_interface(PyObject* mapping, Batch* batch)
{
    auto interface = std::unique_ptr<ParsedInterface>(new (std::nothrow) ParsedInterface());
    if (interface == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }
    if (!PyDict_Check(mapping))
        return schema_error(nullptr, "interfaces", "every interface must be a mapping");

    PyObject* name_object = field(mapping, "name");
    if (name_object == nullptr || name_object == Py_None)
        return schema_error(nullptr, "name", "every interface needs a name");
    const char* name = abi3::Utf8(name_object);
    if (name == nullptr)
        return false;
    interface->strings.emplace_back(name);
    interface->spec.name = interface->strings.back().c_str();
    const char* interface_name = interface->spec.name;

    if (batch->find(interface_name) != nullptr)
        return schema_error(interface_name, "name",
                            "this interface appears twice in the same batch");

    std::string text;
    bool present = false;
    if (!string_field(mapping, "parent", &text, &present))
        return false;
    if (present)
    {
        interface->strings.emplace_back(text);
        interface->spec.parent = interface->strings.back().c_str();
    }

    if (!string_field(mapping, "to_string_tag", &text, &present))
        return false;
    if (present)
    {
        interface->strings.emplace_back(text);
        interface->spec.to_string_tag = interface->strings.back().c_str();
    }

    unsigned traits = 0;
    if (!flag_field(mapping, "traits", kTraitNames, std::size(kTraitNames), 0, &traits))
        return false;
    if (traits > 0xFFFF)
        return schema_error(interface_name, "traits", "too many traits");

    // A trait that is parsed and then not honoured would install an interface
    // that is subtly not what the host declared, which is worse than not
    // installing it at all.
    constexpr unsigned kInstalledTraits = kTraitSkipInherit | kTraitHasConstructor |
                                          kTraitUnconstructable | kTraitGlobalThis |
                                          kTraitImmutablePrototype | kTraitImmutableInstance |
                                          kTraitUndetectable;
    if ((traits & ~kInstalledTraits) != 0)
        return schema_error(interface_name, "traits",
                            "this trait is not installed yet");
    interface->spec.traits = static_cast<std::uint16_t>(traits);

    // Exposed unless the host says otherwise, because a description names an
    // interface in order to install it under that name often enough that the
    // other case is the one worth spelling out.
    long long exposed = 1;
    if (!integer_field(mapping, "exposed", 1, &exposed))
        return false;
    interface->spec.exposed = exposed != 0;

    unsigned behavior = 0;
    if (!flag_field(mapping, "constructor", kConstructNames, std::size(kConstructNames), 0,
                    &behavior))
        return false;
    interface->spec.constructor_behavior = static_cast<std::uint8_t>(behavior);

    long long length = 0;
    if (!integer_field(mapping, "length", 0, &length))
        return false;
    if (length < 0 || length > 0x7FFFFFFF)
        return schema_error(interface_name, "length", "a negative or absurd length");
    interface->spec.length = static_cast<std::uint32_t>(length);

    if (!slot_field(mapping, "type", &interface->spec.type_slot))
        return false;
    if (interface->spec.type_slot != kNoSlot)
    {
        PyObject* candidate = PyTuple_GetItem(
            batch->implementations, static_cast<Py_ssize_t>(interface->spec.type_slot));
        if (candidate == nullptr)
        {
            PyErr_Clear();
            return schema_error(interface_name, "type",
                                "implementations has no entry for this slot");
        }
        if (!PyType_Check(candidate))
            return schema_error(interface_name, "type",
                                "this slot must hold a Python class, not a callback");
        interface->type_object = reinterpret_cast<PyTypeObject*>(candidate);
    }

    // A call handler has no slot when the host declared none, and then no binding is
    // created at all -- an interface whose instances are not callable has nothing to
    // dispatch to, and a binding with no callable would only be a null check later.
    long long array_flags = 0;
    if (!integer_field(mapping, "array", 0, &array_flags))
        return false;
    if (array_flags < 0 || array_flags > 0x1F)
        return schema_error(interface_name, "array",
                            "collection flags are the five intrinsic bits, 0 to 31");
    interface->spec.array_flags = static_cast<std::uint8_t>(array_flags);

    if (!slot_field(mapping, "call", &interface->spec.call_slot))
        return false;
    if (!add_binding(batch, interface.get(), interface->spec.call_slot, kNoSlot,
                     interface->spec.name, nullptr, &interface->spec,
                     &interface->call_binding))
        return false;

    if (!slot_field(mapping, "constructor_slot", &interface->spec.constructor_slot))
        return false;
    if (!add_binding(batch, interface.get(), interface->spec.constructor_slot, kNoSlot,
                     interface->spec.name, nullptr, &interface->spec,
                     &interface->constructor_binding, /*force=*/true))
        return false;

    for (std::uint32_t role = 0; role < kHandlerCount; ++role)
    {
        interface->handler_slots[role] = kNoSlot;
        interface->handler_binding[role] = kNoSlot;
        interface->cross_origin_slots[role] = kNoSlot;
        interface->cross_origin_binding[role] = kNoSlot;
    }
    interface->spec.handlers = interface->handler_slots;
    interface->spec.cross_origin_check = kNoSlot;
    interface->spec.cross_origin_handlers = nullptr;

    PyObject* handlers = field(mapping, "handlers");
    if (handlers != nullptr && handlers != Py_None)
    {
        if (!PyDict_Check(handlers))
            return schema_error(interface_name, "handlers", "handlers must be a mapping");
        if (!parse_handlers(handlers, batch, interface.get(), interface->handler_slots,
                            interface->handler_binding, /*cross_origin=*/false))
            return false;
    }

    PyObject* cross_origin = field(mapping, "cross_origin");
    if (cross_origin != nullptr && cross_origin != Py_None)
    {
        if (!PyDict_Check(cross_origin))
            return schema_error(interface_name, "cross_origin",
                                "cross_origin must be a mapping");
        if (!parse_cross_origin(cross_origin, batch, interface.get()))
            return false;
    }

    PyObject* members = field(mapping, "members");
    if (members == nullptr || members == Py_None)
    {
        interface->spec.members = nullptr;
        interface->spec.member_count = 0;
    }
    else
    {
        PyRef sequence(PyRef::adopt(PySequence_Fast(members, "members must be a sequence")));
        if (!sequence)
            return false;

        const Py_ssize_t count = PySequence_Size(sequence.get());
        if (count < 0)
            return false;
        interface->members.resize(static_cast<std::size_t>(count));
        interface->member_binding.assign(static_cast<std::size_t>(count), kNoSlot);
        for (Py_ssize_t index = 0; index < count; ++index)
        {
            PyRef item(PyRef::adopt(PySequence_GetItem(sequence.get(), index)));
            if (!item)
                return false;
            if (!parse_member(item.get(), batch, interface.get(),
                              &interface->members[static_cast<std::size_t>(index)],
                              &interface->member_binding[static_cast<std::size_t>(index)]))
                return false;
        }
        // Taken after the last push: the descriptor holds a pointer, and a vector
        // that grew would have moved what it pointed at.
        interface->spec.members = interface->members.data();
        interface->spec.member_count = static_cast<std::uint32_t>(interface->members.size());
    }

    batch->interfaces.push_back(std::move(interface));
    return true;
}

}  // namespace

bool
schema_error(const char* interface_name, const char* field_name, const char* problem)
{
    if (interface_name == nullptr)
        raise(PyExc_ValueError, "interface description: %s (%s)", problem, field_name);
    else
        raise(PyExc_ValueError, "%s: %s (%s)", interface_name, problem, field_name);
    return false;
}

// Reads the names the global object exposes without building them, and the one
// factory that answers for any of them.
//
// The name is the key and the factory is shared, rather than a mapping from name to
// callable, because a host that compiles its interfaces in one place has one
// function that can do it for every name.
bool
parse_lazy(PyObject* description, Batch* batch)
{
    PyObject* names = field(description, "lazy");
    if (names == nullptr || names == Py_None)
        return true;

    // Sized and indexed rather than PySequence_Fast: the fast macros are not in the
    // limited API, and a list of names is not a hot path.
    const Py_ssize_t count = PySequence_Size(names);
    if (count < 0)
        return false;

    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(names, index)));
        if (!item)
            return false;

        const char* text = abi3::Utf8(item.get());
        if (text == nullptr)
        {
            PyErr_Clear();
            raise(PyExc_TypeError, "lazy[%zd] is not a string", index);
            return false;
        }

        auto binding = std::unique_ptr<LazyBinding>(new (std::nothrow) LazyBinding());
        if (binding == nullptr)
        {
            PyErr_NoMemory();
            return false;
        }
        // Copied: the value belongs to the description, which is released when the
        // call returns, while the getter outlives it by the life of the realm.
        binding->realm = batch->realm;
        binding->name = text;
        batch->lazy.push_back(std::move(binding));
    }

    if (batch->lazy.empty())
        return true;

    std::uint32_t factory_slot = kNoSlot;
    if (!slot_field(description, "lazy_factory", &factory_slot))
        return false;
    if (factory_slot == kNoSlot)
    {
        raise(PyExc_ValueError, "a lazy name needs a lazy_factory to build it");
        return false;
    }
    return resolve_slot(batch, "lazy_factory", factory_slot, &batch->lazy_factory);
}

Batch::~Batch()
{
    Py_XDECREF(implementations);
    for (std::unique_ptr<ParsedInterface>& interface : interfaces)
    {
        if (interface->template_slot != nullptr)
        {
            interface->template_slot->reset();
            delete interface->template_slot;
            interface->template_slot = nullptr;
        }
    }
}

ParsedInterface*
Batch::find(const char* name) const
{
    for (const std::unique_ptr<ParsedInterface>& interface : interfaces)
    {
        if (std::strcmp(interface->spec.name, name) == 0)
            return interface.get();
    }
    return nullptr;
}

void
release_batch(void* state)
{
    delete static_cast<Batch*>(state);
}

MemberBinding*
binding_for(Batch& batch, std::uint32_t index)
{
    if (index >= batch.bindings.size())
    {
        raise(PyExc_RuntimeError, "the batch has no binding %u", index);
        return nullptr;
    }
    return batch.bindings[index].get();
}

bool
parse_batch(Realm& realm, PyObject* description, Batch* batch)
{
    batch->realm = &realm;

    if (!PyDict_Check(description))
        return schema_error(nullptr, "description", "a batch description must be a mapping");

    PyObject* implementations = field(description, "implementations");
    if (implementations == nullptr || implementations == Py_None)
        return schema_error(nullptr, "implementations",
                            "a batch description needs a tuple of callables");
    if (!PyTuple_Check(implementations))
        return schema_error(nullptr, "implementations", "implementations must be a tuple");
    batch->implementations = PyRef::borrow(implementations).release();

    PyRef interfaces(PyRef::adopt(
        PySequence_Fast(field(description, "interfaces"), "interfaces must be a sequence")));
    if (!interfaces)
        return false;

    const Py_ssize_t count = PySequence_Size(interfaces.get());
    if (count < 0)
        return false;
    batch->interfaces.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(interfaces.get(), index)));
        if (!item)
            return false;
        if (!parse_interface(item.get(), batch))
            return false;
    }

    if (!parse_lazy(description, batch))
        return false;

    // Two interfaces claiming one Python type is a description mistake, and it has
    // to be caught here rather than while installing: which template an instance
    // gets would otherwise depend on install order, and a batch that failed halfway
    // would leave the first claim behind.
    for (std::size_t index = 0; index < batch->interfaces.size(); ++index)
    {
        PyTypeObject* type = batch->interfaces[index]->type_object;
        if (type == nullptr)
            continue;

        for (std::size_t other = index + 1; other < batch->interfaces.size(); ++other)
        {
            if (batch->interfaces[other]->type_object == type)
            {
                raise(PyExc_ValueError, "%s and %s both claim one Python type",
                      batch->interfaces[index]->spec.name,
                      batch->interfaces[other]->spec.name);
                return false;
            }
        }
    }
    return true;
}

}  // namespace v8n::bridge

namespace v8n
{

const char*
handler_role_name(HandlerRole role)
{
    // One name per role, in HandlerRole order.  The assertion is what keeps the
    // two lists -- the roles and their names -- from drifting apart silently.
    static const char* const kNames[] = {
        "name_get", "name_set", "name_query", "name_del", "name_enum",
        "index_get", "index_set", "index_query", "index_del", "index_enum",
    };
    static_assert(std::size(kNames) == kHandlerCount, "every handler role needs a name");

    const unsigned index = static_cast<unsigned>(role);
    return index < kHandlerCount ? kNames[index] : "handler";
}

}  // namespace v8n
