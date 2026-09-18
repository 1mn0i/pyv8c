#ifndef V8N_BRIDGE_H
#define V8N_BRIDGE_H

// The interface bridge.
//
// Everything a host installs into a realm is described in one flat batch and
// handed over in one call.  The previous binding went the other way: it compiled
// one member at a time and committed each one as it went, so building a page cost
// O(interfaces x members) crossings and every crossing could fail on its own.  A
// batch is a single crossing that either installs everything or reports where it
// stopped, and the description is plain data -- strings, integers, and one tuple
// of callables -- so the shape of what a host declares is also the shape the
// native side reads.
//
// The callback for a member is not resolved here.  A host that wants a Python
// method to answer a JavaScript property access resolves the method itself and
// passes the callable in `implementations`; the description refers to it by
// index.  Resolution is host policy (pyv8c injects a realm into some of its
// callbacks, and not into others), and keeping it on the Python side is what
// makes the batch flat.

#include "v8n/realm.h"
#include "v8n/v8.h"

#include <cstdint>

namespace v8n
{

class Realm;

// What a member is.
enum class MemberKind : std::uint8_t
{
    // A property with a getter, and a setter when the slot is present.
    kAttribute,
    // A function.
    kMethod,
    // A number, installed on both the prototype and the interface.
    kConstant,
    // A number produced by a callback, installed as a lazy data property so the
    // callback runs only if something reads it.
    kConstantCallback,
};

// Where a member lives.
enum : std::uint8_t
{
    kLocationInstance = 0,   // each instance
    kLocationPrototype = 1,  // the prototype
    kLocationInterface = 2,  // the constructor itself, which is what "static" means
};

// Per member flags.
enum : std::uint8_t
{
    // Check that the receiver is an object of this interface before dispatching.
    // Ignored at the interface location, where there is no receiver to check.
    kMemberReceiverCheck = 1u << 0,
    // An attribute with a setter.  Without it the property is read-only.
    kMemberHasSetter = 1u << 1,
    // Refuse the read/write when the caller's realm is not the receiver's.
    kMemberCrossOriginGetCheck = 1u << 2,
    kMemberCrossOriginSetCheck = 1u << 3,
    // An attribute whose write replaces itself with a data property.
    kMemberReplaceable = 1u << 4,
    // Refuse the call when the caller's realm is not the receiver's.
    kMemberCrossOriginCheck = 1u << 5,
};

// Per interface traits.
enum : std::uint16_t
{
    // Install this interface as the realm's global object rather than only
    // exposing its name.
    kTraitGlobalThis = 1u << 0,
    // `document.all`-style: a value that is `== null` and `typeof undefined`.
    kTraitUndetectable = 1u << 1,
    // Constructing throws, as for an interface with no constructor operation.
    kTraitUnconstructable = 1u << 2,
    // Expose an instance of this interface on the global instead of a
    // constructor.
    kTraitAsObject = 1u << 3,
    // Do not build a prototype chain to the parent in this batch.  Used when the
    // parent is installed elsewhere.
    kTraitSkipInherit = 1u << 4,
    // Keep the synthetic `constructor` property on the prototype.
    kTraitHasConstructor = 1u << 5,
    // Make instances or the prototype non-extensible.
    kTraitImmutableInstance = 1u << 6,
    kTraitImmutablePrototype = 1u << 7,
};

// Which call forms a constructor accepts.  Both clear means "illegal
// constructor", which is what a namespace or a mixin interface reports.
enum : std::uint8_t
{
    kConstructNew = 1u << 0,
    kConstructCall = 1u << 1,
};

// What an interface's instances offer as an indexed collection.
//
// The engine supplies each of these as an intrinsic, so an interface that says it
// is a collection is iterable without its host writing a single accessor: the
// intrinsic reads `length` and the indexed handler, which the host already
// answers for everything else.
//
// The bit values are a contract with the host's declaration rather than an
// invention here; a declaration that names them in this order is what the bits
// mean.
enum : std::uint8_t
{
    kArrayIterator = 1u << 0,
    kArrayEntries = 1u << 1,
    kArrayKeys = 1u << 2,
    kArrayValues = 1u << 3,
    kArrayForEach = 1u << 4,
};

// No callback, for an attribute without a setter, an interface without a
// constructor adapter, or a handler a host did not declare.
constexpr std::uint32_t kNoSlot = 0xFFFFFFFFu;

// The property operations a host can answer for itself instead of declaring a
// member for every name.
//
// A host object's property set is not known in advance -- a window has as many
// properties as the page asks for -- so the interface declares handlers and the
// host answers per name.  The order here is the order InterfaceSpec::handlers
// lists them in.
enum HandlerRole : std::uint8_t
{
    kHandlerNameGet,
    kHandlerNameSet,
    kHandlerNameQuery,
    kHandlerNameDel,
    kHandlerNameEnum,

    kHandlerIndexGet,
    kHandlerIndexSet,
    kHandlerIndexQuery,
    kHandlerIndexDel,
    kHandlerIndexEnum,

    kHandlerCount,
};

// What each handler receives and what it may answer.
//
// Every handler is called with the receiver first, because a handler belongs to an
// interface rather than to an object: without it the handler could not tell which of
// the interface's objects is being asked, and it is the object -- not the interface
// -- that owns the answer.
//
//   get   (self, name)          -> a value, or None for "not mine"
//   set   (self, name, value)   -> anything, or None for "not mine"
//   query (self, name)          -> the property's attribute mask, or None for
//                                  "no opinion"; True as a shorthand for "present
//                                  with no attributes"
//   del   (self, name)          -> a bool: whether it was deleted
//   enum  (self)                -> a sequence of names, or None.  The indexed form
//                                  answers with a count instead, because an
//                                  enumerator that had to build a list to say
//                                  "there are a thousand" would allocate a thousand
//                                  entries to answer a question about a number.
//
// "Not mine" is what lets the answer fall through to the prototype chain, which is
// why it is a distinct answer from a null value: a host object that returned an
// actual null everywhere would shadow everything above it.
//
// A `query` answers with the attribute mask rather than a bool because that is what
// V8 asks for: the mask is what `in`, enumeration and property descriptors report,
// so "present, and read-only" is an answer a bool has no way to express.
const char* handler_role_name(HandlerRole role);

struct MemberSpec
{
    // The name JavaScript sees.
    const char* name;
    MemberKind kind;
    // One of the kLocation constants.
    std::uint8_t location;
    // kMember* bits.
    std::uint8_t flags;
    // Straight into v8::PropertyAttribute: ReadOnly, DontEnum, DontDelete.
    std::uint8_t property_flags;
    // Straight into v8::SideEffectType.
    std::uint8_t side_effect;
    // The declared function length, for a method.
    std::uint8_t arity;
    // The value, for kConstant.
    std::int32_t constant;
    // Index into BatchSpec::implementations.  kNoSlot for a constant.
    std::uint32_t slot;
    // Index of the setter, for an attribute.  kNoSlot when there is none.
    std::uint32_t setter_slot;
};

struct InterfaceSpec
{
    // The interface name, which is also the constructor's name.
    const char* name;
    // Whether installing this interface also writes its name onto the global
    // object.
    //
    // Installing and exposing are two different things, and a host needs both:
    // an interface can be in a batch because something else needs it -- a child
    // needs its parent, an object of a Python type needs its prototype -- and
    // neither is a name JavaScript should find on `window`.
    bool exposed;
    // The parent interface's name, or null.  The parent must be in this batch,
    // or already installed.
    const char* parent;
    // kTrait* bits.
    std::uint16_t traits;
    // kConstruct* bits.
    std::uint8_t constructor_behavior;
    // kArray* bits.  Clear for an interface that is not a collection.
    std::uint8_t array_flags;
    // The constructor's length.
    std::uint32_t length;
    // Symbol.toStringTag, or null.
    const char* to_string_tag;
    const MemberSpec* members;
    std::uint32_t member_count;
    // Index of a constructor adapter, called as adapter(args, kwargs).
    // kNoSlot means the interface cannot be constructed.
    std::uint32_t constructor_slot;
    // Index of a handler that makes instances callable, called as
    // handler(instance, ...args), or kNoSlot.  An interface that declares one is
    // one whose objects JavaScript may invoke directly, which is what
    // `document.all`-style and callback-style objects have in common.
    //
    // The result is a callable object, not a function: it reports `typeof
    // "function"` and carries the interface's members and prototype, but it has no
    // `length` or `name` of its own, because those belong to a function object and
    // this is not one.  A host that wants a `length` declares it as a member.
    std::uint32_t call_slot;
    // Index of the Python type this interface stands for, or kNoSlot.  The entry
    // is a class, not a callback: it is what lets an instance built in Python find
    // this interface's prototype instead of arriving as a generic host object.
    std::uint32_t type_slot;
    // kHandlerCount slots, indexed by HandlerRole.  kNoSlot where the host
    // declared nothing for that role.
    const std::uint32_t* handlers;
    // A callable that decides whether another realm may touch this interface's
    // objects, or kNoSlot.  It is handed the Python object of the object's own
    // realm and the Python object of the realm asking, in that order, and denies
    // only by returning False exactly.
    //
    // Its failure is meaningful too: an AttributeError means "no policy", and
    // anything else is a policy the host expressed by failing, reported in the
    // asking realm.
    std::uint32_t cross_origin_check;
    // Handlers used only when the access is cross-origin, so an interface can
    // answer a stranger differently from a neighbour.  kHandlerCount slots, or
    // null when the host declared none -- in which case the same-origin handlers
    // are not reused, because leaking them across origins is the one thing a
    // cross-origin policy exists to prevent.
    const std::uint32_t* cross_origin_handlers;
};

// The native-side shape of a batch.
//
// A host does not build this: it writes the description below, which is read
// into these structures once, in one crossing, and never crosses back.  They are
// documented because they are the contract the description is read against.
struct BatchSpec
{
    const InterfaceSpec* interfaces;
    std::uint32_t interface_count;
    // A tuple of callables, indexed by MemberSpec::slot.
    PyObject* implementations;
};

// Installs a batch from its description and returns false with a Python error
// set, having installed nothing: the whole description is validated and every
// template is built before anything is written into the context.
//
// The description is a mapping:
//
//   {"interfaces": [interface, ...],
//    "implementations": (callable, ...),
//    "lazy": [str, ...],               # exposed on the global object, built on
//                                      # first read
//    "lazy_factory": int | None}       # slot; called as factory(name), answering
//                                      # with a batch description for that name or
//                                      # None when the name is not this realm's
//
// A lazy name costs one getter that nothing calls and nothing else, which is the
// difference between a realm that builds every interface it could ever expose and
// one that builds the interfaces a page actually touches.  The factory answers with
// a description rather than with an object because the description goes through the
// same install path as any other batch: there is one implementation of building an
// interface, and this is not a second one.
//
// and an interface is a mapping:
//
//   {"name": str,                       # required; also the constructor's name
//    "parent": str | None,              # another interface in this batch
//    "to_string_tag": str | None,
//    "length": int,                    # the constructor's length
//    "traits": [str, ...],             # see kTrait* below
//    "constructor": [str, ...],        # "new", "call"; empty means illegal
//    "constructor_slot": int | None,   # adapter, called as (args, kwargs)
//    "call": int | None,               # makes instances callable, (self, *args)
//    "array": int,                     # kArray* bits; 0 when not a collection
//    "type": int | None,               # implementations entry holding the class
//    "handlers": {role: int, ...},     # per-name answers, see HandlerRole
//    "cross_origin": {role: int, "check": int},
//    "members": [member, ...]}
//
// and a member is a mapping:
//
//   {"kind": "attribute" | "method" | "constant" | "constant_callback",
//    "name": str,                      # required; the name JavaScript sees
//    "location": "instance" | "prototype" | "interface",
//    "flags": [str, ...],              # see kMember* below
//    "property_flags": int,            # the three V8 attribute bits
//    "side_effect": int,               # 0, 1 or 2
//    "arity": int,                     # a method's declared length
//    "value": int,                     # a constant's value
//    "slot": int | None,               # index into implementations
//    "setter_slot": int | None}        # an attribute's setter, with has_setter
//
// A field that is not installed yet is refused rather than ignored, so a
// description cannot silently mean less than it says.
bool install_batch(Realm& realm, PyObject* description);

}  // namespace v8n

#endif
