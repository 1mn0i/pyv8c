#ifndef V8N_BRIDGE_INTERNAL_H
#define V8N_BRIDGE_INTERNAL_H

// What the bridge shares between its translation units.  The contract a host
// sees is in v8n/bridge.h.

#include "v8n/bridge.h"
#include "v8n/kernel.h"
#include "v8n/v8.h"

#include <deque>
#include <memory>
#include <string>
#include <vector>

namespace v8n
{

class Realm;
struct PythonOwner;

namespace bridge
{

// One member's JavaScript callback target.
//
// The callable is borrowed from the batch, which holds a single reference to
// every callable the description refers to.  Reaching it from a V8 callback goes
// through the External that V8 hands back as the callback's data, so a dispatch
// is a pointer dereference rather than a name lookup, and the hot path allocates
// nothing.
struct MemberBinding
{
    Realm* realm = nullptr;
    // The getter, the method, or the constructor adapter.
    PyObject* callable = nullptr;
    // The setter of an attribute, or null.  One binding covers both halves, so an
    // accessor needs a single pointer of data rather than two.
    PyObject* setter = nullptr;
    const char* member_name = nullptr;
    // Null for a member; the interface for a constructor, which is where the
    // trait bits and the name come from.
    const InterfaceSpec* interface_spec = nullptr;
    const MemberSpec* spec = nullptr;
};

// The data V8 hands to every interceptor of one interface.
//
// A handler configuration carries a single data for all of its callbacks, so the
// bindings are collected here and each callback picks the role it is: the
// function knows which role it implements, the data only has to hold all of them.
struct HandlerData
{
    Realm* realm = nullptr;
    MemberBinding* roles[kHandlerCount] = {};
};

// The same table for the cross-origin case.  A separate object rather than a
// second field, because the callbacks are identical and it is the data that says
// which set of bindings answered: a handler configuration carries one data, and
// the cross-origin configuration carries this one.
using CrossOriginData = HandlerData;

// One interface, plus everything the batch has to keep alive while installing it.
struct ParsedInterface
{
    // Owns every string the descriptors point at.  A deque rather than a vector
    // because the descriptors hold raw pointers into it and push_back must not
    // move what it already handed out.
    std::deque<std::string> strings;
    std::vector<MemberSpec> members;
    // One slot per handler role, in HandlerRole order, and the binding each one
    // dispatches through.  Arrays rather than a struct because the roles are
    // already an enumeration and a struct would be a second place to keep the
    // same order.
    std::uint32_t handler_slots[kHandlerCount] = {};
    std::uint32_t handler_binding[kHandlerCount] = {};
    std::uint32_t cross_origin_slots[kHandlerCount] = {};
    std::uint32_t cross_origin_binding[kHandlerCount] = {};
    // Stable, because they are the data every interceptor of this interface was
    // installed with.
    HandlerData handler_data;
    CrossOriginData cross_origin_data;
    InterfaceSpec spec{};
    // Parallel to members: the index of the binding used as the getter's or the
    // method's data, and the index used as the setter's.  kNoSlot where there is
    // none.  Kept out of MemberSpec so the description a host writes stays data
    // rather than a place to hang pointers.
    std::vector<std::uint32_t> member_binding;
    // The index of the constructor adapter's binding, or kNoSlot.
    std::uint32_t constructor_binding = kNoSlot;
    // The index of the binding that makes instances callable, or kNoSlot.
    std::uint32_t call_binding = kNoSlot;
    // The index of the cross-origin policy's binding, or kNoSlot.
    std::uint32_t cross_origin_check_binding = kNoSlot;
    // The Python type this interface stands for, borrowed from the batch's
    // implementations, or null.  Registered with the realm so an instance built in
    // Python finds this interface's prototype.
    PyTypeObject* type_object = nullptr;
    // Held only while the batch installs.  V8 keeps a template alive through the
    // function it produced, but Inherit needs the parent's *template*, and a
    // parent may be built before the child that inherits from it.
    HandleSlot<v8::FunctionTemplate>* template_slot = nullptr;
};

// One name the global object exposes without building it.
//
// The factory is borrowed from the batch, like every other callable.  The name is
// owned here because V8 hands this back as the getter's data with no other way to
// know which lazy property fired.
struct LazyBinding
{
    Realm* realm = nullptr;
    PyObject* factory = nullptr;
    std::string name;
};

// A batch, owned by the realm it installed into.
struct Batch
{
    Realm* realm = nullptr;
    // The description's tuple of callables, with one reference held here for as
    // long as the realm can dispatch through it.
    PyObject* implementations = nullptr;
    std::vector<std::unique_ptr<ParsedInterface>> interfaces;
    // Stable after parse, because a callback's data points at one.
    std::vector<std::unique_ptr<MemberBinding>> bindings;
    // One per name the global object exposes lazily.  Stable, because the getter's
    // data points at one.
    std::vector<std::unique_ptr<LazyBinding>> lazy;
    // The one callable that answers for every lazy name, borrowed from
    // implementations, or null when the batch declared no lazy names.
    PyObject* lazy_factory = nullptr;

    ~Batch();

    ParsedInterface* find(const char* name) const;
    // The interface marked kTraitGlobalThis, or null.
    ParsedInterface* global_interface() const;
    // The interface marked kTraitGlobalThis that *host* is an instance of.
    //
    // A batch may declare more than one, because the interfaces a window exposes
    // include the ones a worker scope is built from.  The host object's type is
    // what says which of them this realm's global object stands for.
    ParsedInterface* global_interface(PyObject* host) const;
};

// Builds every template, parents before children.  Needs only the isolate, which
// is what lets a realm build the global template before its context exists --
// V8 takes the global template at Context::New and offers no way to add an
// interceptor to the global object afterwards.
//
// global_this, when given, is the object the realm will be created around; it is
// what picks the global interface when the batch declares more than one.
bool build_templates(Isolate& isolate, Batch& batch, PyObject* global_this = nullptr);

// Turns the templates into functions, installs the static members, and exposes
// the names.  Everything that needs a context.
//
// deferred_name, when given, is a name not to write onto the global object, and
// deferred_value receives its function instead.  That is the lazy case: the caller
// is the getter behind a lazily materialized name, V8 is mid-initialization on that
// very property, and writing it here as well would be writing it twice -- into a
// property whose lookup state is still the lazy one, which V8 checks against.  V8
// caches whatever the getter returns, so the value goes back to the getter instead.
bool finish_batch(Realm& realm, v8::Local<v8::Context> context, Batch& batch,
                  const char* deferred_name = nullptr,
                  v8::Local<v8::Value>* deferred_value = nullptr);

// The release function handed to Realm::own_state.
void release_batch(void* state);

// Writes to *out with a Python error set, naming the interface and the field.
bool schema_error(const char* interface_name, const char* field, const char* expected);

// -- schema.cpp -----------------------------------------------------------

// Reads a description into a batch.  Nothing is installed and no template is
// built: a description that is wrong at all is rejected before the context is
// touched, so a failed install leaves the realm as it was.
bool parse_batch(Realm& realm, PyObject* description, Batch* batch);

// The binding for a slot, or nullptr with a Python error set.
MemberBinding* binding_for(Batch& batch, std::uint32_t slot);

// -- template_builder.cpp -------------------------------------------------

// Builds the FunctionTemplate for one interface, installing its instance and
// prototype members.  parent_template is the template to inherit from, already
// resolved by build_templates -- which is the layer that knows whether the
// parent is in this batch or in one the realm installed earlier.  Interface
// members are skipped here: they belong on the function object, which does not
// exist yet.
//
// Needs no context, which is the whole reason it is separate.
v8::Local<v8::FunctionTemplate> build_template(
    v8::Isolate* isolate, ParsedInterface& interface, Batch& batch,
    v8::Local<v8::FunctionTemplate> parent_template);

// Installs the members that live on the function object itself -- what
// JavaScript calls static members, and IDL calls interface members.
bool install_interface_members(v8::Isolate* isolate, v8::Local<v8::Context> context,
                               v8::Local<v8::Function> function, ParsedInterface& interface,
                               Batch& batch);

// Installs the named and indexed property handlers an interface declared, on its
// instance template.  Returns false with a Python error set.
bool install_handlers(v8::Isolate* isolate, ParsedInterface& interface, Batch& batch,
                      v8::Local<v8::FunctionTemplate> interface_template);

// -- dispatch.cpp ---------------------------------------------------------

// The V8 callbacks.  Each recovers its MemberBinding from the data V8 hands
// back, which is why none of them looks anything up by name.
// Accessors, installed as function templates through SetAccessorProperty.
//
// Not SetNativeDataProperty, and the reason is the receiver: a native accessor is
// handed a PropertyCallbackInfo, which has no way to reach the object the
// property was read through.  Holder() is where the accessor was found, which for
// an attribute declared on the prototype is the prototype, and the Python
// identity lives on the instance.  A function callback has This(), so an accessor
// function template is the form that can answer the only question that matters
// here.
void attribute_getter(const v8::FunctionCallbackInfo<v8::Value>& info);
void attribute_setter(const v8::FunctionCallbackInfo<v8::Value>& info);
void constant_getter(const v8::FunctionCallbackInfo<v8::Value>& info);
// A lazy constant is the one accessor that genuinely has no receiver -- the value
// is produced for whoever asks -- so it can use the native data property form,
// which is the only one V8 offers for lazy properties.
void lazy_constant_getter(v8::Local<v8::Name> name,
                          const v8::PropertyCallbackInfo<v8::Value>& info);

// Interface members -- statics -- take no receiver either, for the same reason: a
// static member is not an operation on an instance.  That makes the native data
// property form usable here, and these are the two callbacks it needs.
void static_attribute_getter(v8::Local<v8::Name> name,
                             const v8::PropertyCallbackInfo<v8::Value>& info);
void static_attribute_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value,
                             const v8::PropertyCallbackInfo<void>& info);

// -- interceptors ---------------------------------------------------------
//
// The V2 form, which returns whether the host answered.  That return value is the
// whole design: an interface with a handler has an opinion about names it has
// never heard of, and saying "not mine" is how a lookup continues to the
// prototype chain instead of being shadowed by it.
v8::Intercepted named_getter(v8::Local<v8::Name> name,
                            const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted named_setter(v8::Local<v8::Name> name, v8::Local<v8::Value> value,
                             const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted named_query(v8::Local<v8::Name> name,
                            const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted named_deleter(v8::Local<v8::Name> name,
                              const v8::PropertyCallbackInfo<v8::Boolean>& info);
void named_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

v8::Intercepted indexed_getter(uint32_t index,
                               const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted indexed_setter(uint32_t index, v8::Local<v8::Value> value,
                               const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted indexed_query(uint32_t index,
                              const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted indexed_deleter(uint32_t index,
                                const v8::PropertyCallbackInfo<v8::Boolean>& info);
void indexed_enumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

// Asked before another realm touches one of this interface's objects.  It
// answers with the object's own realm deciding, so a policy belongs to the page
// being touched rather than to whoever is touching it.
bool cross_origin_access_check(v8::Local<v8::Context> accessing_context,
                               v8::Local<v8::Object> accessed_object, v8::Local<v8::Value> data);
// The getter behind a lazily materialized global name.
//
// It answers by installing whatever the factory describes and returning what the
// install exposed, so there is one implementation of building an interface rather
// than one for the eager case and one for this.
void lazy_interface_getter(v8::Local<v8::Name> name,
                           const v8::PropertyCallbackInfo<v8::Value>& info);
void method_call(const v8::FunctionCallbackInfo<v8::Value>& info);
// A host object invoked as a function.  Separate from method_call because the
// receiver is not a question here: the callee is the receiver.
void instance_call(const v8::FunctionCallbackInfo<v8::Value>& info);
void interface_construct(const v8::FunctionCallbackInfo<v8::Value>& info);

// The Python instance behind a JavaScript receiver, borrowed, or null when the
// receiver is not a host object of this realm.
PyObject* receiver_of(v8::Local<v8::Object> holder, v8::Isolate* isolate);

// Emits one trace event, when the realm is tracing.
void trace_event(Realm& realm, TraceEvent event, const char* member);

// -- batch.cpp ------------------------------------------------------------

// Builds every template, gets every function, installs every member, exposes the
// globals.  The batch is handed to the realm, which releases it at close.
//
// deferred_name and deferred_value are passed through to finish_batch; see there.
bool install_description(Realm& realm, PyObject* description, const char* deferred_name = nullptr,
                         v8::Local<v8::Value>* deferred_value = nullptr);

}  // namespace bridge
}  // namespace v8n

#endif
