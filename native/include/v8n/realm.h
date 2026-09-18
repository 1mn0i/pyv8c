#ifndef V8N_REALM_H
#define V8N_REALM_H

// A realm: one JavaScript global, its context, and everything the host installs
// into it.  It is the unit pyv8c calls a page or a worker, and the unit that
// owns V8 handles, so closing one releases exactly what it created.
//
// The lifecycle is a state machine rather than a boolean, because closing is not
// instantaneous: entry has to be refused first, work already inside has to
// finish, and only then can the resources go.  See lifecycle.cpp.
//
// The realm is also the only place a Python reference may be held on JavaScript's
// behalf.  Hooks, microtask callables and the trace hook all register the address
// of the field that owns them with the realm's root set, so one traversal and one
// teardown cover every one of them; see resources.h.

#include "v8n/kernel.h"
#include "v8n/v8.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace v8n
{

class Isolate;
struct PythonOwner;
class ResourceRegistry;
class TimeoutGuard;

// How a realm is built.
struct RealmOptions
{
    // Name this realm reports in error messages and in the Inspector.  Null
    // means "realm".
    const char* name = nullptr;
    // Whether the realm gets its own microtask queue.  Without one, promise
    // reactions and queueMicrotask are refused rather than silently dropped,
    // because a host that installs hooks has to know whether they will run.
    bool microtasks = true;
    // Wall-clock budget for one entry, in milliseconds.  0 means no limit.  A
    // runaway script is terminated and reported as an error, not left to hang
    // the process.
    long long timeout_ms = 0;
    // The Python object that should become this realm's global object, borrowed.
    // Null means the realm gets an ordinary JavaScript global.
    //
    // This is a construction parameter rather than something set afterwards
    // because the global object's template has to exist before the context does:
    // V8 takes the global template at Context::New and offers no way to give the
    // global object an interceptor later.
    PyObject* global_this = nullptr;
    // A batch description to install as part of creating the realm, borrowed.  It
    // is read here rather than afterwards so that the interface marked
    // kTraitGlobalThis can supply the global template.
    PyObject* description = nullptr;
};

// What one close attempt did.
//
// Closing is not atomic: wrapper records are released, then the root set, then
// retained handles, then the context.  A step that cannot run -- because the
// caller is not the isolate's owner thread -- is reported instead of forced, and
// close() may be called again to finish.  That is what retryable means here:
// nothing that already succeeded is repeated, and nothing that failed is
// pretended to have happened.
struct CloseReport
{
    // True when the realm is closed by the end of the call.
    bool closed = false;
    // True when a step was refused and a later close() on the owner thread can
    // still finish the job.
    bool retryable = false;
    // How much this call released.
    std::size_t owners_released = 0;
    std::size_t roots_released = 0;
    // Why the realm is still closing, or null.  Points at a string literal.
    const char* blocked_by = nullptr;
};

// What happened, for the trace hook and for anyone reading a log.
enum class TraceEvent : std::uint8_t
{
    kGet,
    kSet,
    kCall,
    kConstruct,
    kHas,
    kDescriptor,
    kEnumerate,
};

// The name of a trace event, as the hook receives it.
const char* trace_event_name(TraceEvent event);

class Realm
{
public:
    enum class State : std::uint8_t
    {
        kActive,   // entry allowed
        kClosing,  // entry refused, in-flight work finishing
        kClosed,   // context destroyed, handles released
    };

    // Creates a realm on the isolate owned by the calling thread.  Returns
    // nullptr with a Python error set when the isolate is not usable.
    static Realm* create(Isolate* isolate);
    static Realm* create(Isolate* isolate, const RealmOptions& options);
    static void destroy(Realm* realm);

    Isolate& isolate() const { return *isolate_; }
    v8::Local<v8::Context> context() const;

    // The realm's global object, as a handle.  A host that wants to put something
    // on the global -- a bridge object, a build identifier -- needs it, and
    // reaching it any other way would mean exporting the raw context.
    PyObject* global_object();

    // A handle on a Python object's JavaScript counterpart.
    //
    // The same object always gets the same wrapper, so this is also the way to
    // reach an object JavaScript already holds: a host that wants to read or
    // write properties of a Python object from Python -- to operate on a page's
    // own window, say -- needs a handle rather than a value.
    PyObject* wrap(PyObject* object);

    State state() const { return state_; }
    bool is_active() const { return state_ == State::kActive; }
    bool is_closed() const { return state_ == State::kClosed; }
    const char* name() const { return name_; }

    // Refuses entry once the realm is closing, mirroring what the boundary
    // guard does for a Python call.
    bool require_active(const char* operation) const;

    // Compiles and runs source.  filename may be null.  The result crosses the
    // three-state boundary: a primitive, the original Python host object, or a
    // new JavaScript handle.
    PyObject* evaluate(const char* source, const char* filename);

    // Closes the realm.  Idempotent, and every step that can fail is attempted
    // before the result is reported, so a partially closed realm is still a
    // closed one from the caller's point of view.
    bool close();
    // The same, reporting what happened.  See CloseReport.
    bool close(CloseReport* report);

    // -- the Python root set (resources.cpp) ------------------------------
    //
    // register_root records the address of a field that owns a Python
    // reference.  The field's address must not move.  register_root returns
    // false with a Python error set when the registry cannot grow.
    bool register_root(PyObject** slot);
    void unregister_root(PyObject** slot);
    // The tp_traverse protocol over the root set.  A borrowed reference the
    // realm does not own is not a root and is visited by the Python type.
    int traverse(visitproc visit, void* arg) const;
    std::size_t root_count() const;

    // -- error hooks (hooks.cpp) ------------------------------------------
    //
    // Called when JavaScript fails where no Python caller is waiting: an
    // uncaught error inside a callback or a microtask, and a promise rejection
    // nothing handled.  Both receive one argument, the error value.
    //
    // Held as roots, so an installed hook is reachable from this realm and is
    // released when it closes.  Passing null removes the hook.
    bool set_error_hooks(PyObject* on_uncaught, PyObject* on_rejection);
    void report_uncaught(v8::Local<v8::Value> exception);
    // Called as ``(event, identity, promise, reason)`` for both halves of the
    // unhandled-rejection algorithm: a rejection nothing handled, and a handler
    // that appears afterwards.  Both are needed -- the first notice is only a
    // candidate until the checkpoint that could still revoke it has passed, and
    // the host is the only layer that can decide when that checkpoint is over.
    void report_rejection(v8::Local<v8::Promise> promise, v8::Local<v8::Value> reason,
                          bool unhandled);
    bool has_uncaught_hook() const { return uncaught_hook_ != nullptr; }
    bool has_rejection_hook() const { return rejection_hook_ != nullptr; }

    // -- microtasks (microtask.cpp) ---------------------------------------
    //
    // False when the realm was built without microtasks, or when the build could
    // not verify v8::MicrotaskQueue's layout.  In the second case
    // enqueue_microtask refuses instead of calling through a vtable slot that
    // may not be there.
    bool microtasks_supported() const { return microtasks_enabled_; }
    // Queues a Python callable to run at the next checkpoint.  Refuses when the
    // realm has no queue.
    bool enqueue_microtask(PyObject* callable);
    // Drain the queue.  Called at the end of every entry, so a promise chains
    // without the host asking.
    bool run_microtasks();

    // -- execution timeout (timeout.cpp) ----------------------------------
    //
    // Arms a wall-clock budget for entries into this realm.  The guard watches
    // from another thread and calls TerminateExecution, which is the only way to
    // stop a JavaScript loop that never yields.
    bool set_timeout(long long milliseconds);
    long long timeout() const { return timeout_ms_; }
    // Terminate the current entry early with a reason.  Safe to call from
    // another thread; that is the point.
    void terminate(const char* reason);
    // True when the last entry ended because it ran out of time.
    bool was_terminated() const;

    // -- preparing an object to cross (value.cpp) -------------------------
    //
    // Called with a Python instance whose type has no interface installed in
    // this realm, just before that instance is wrapped for JavaScript.  A host
    // that builds interfaces on demand uses it to install one, so the object
    // carries its interface's prototype and members instead of arriving as a
    // generic host object whose properties are Python attributes.
    //
    // Held as a root, so it is released when the realm closes.  Passing null
    // removes it, and a realm without one wraps every object as before.
    bool set_type_resolver(PyObject* callable);
    PyObject* type_resolver() const { return type_resolver_; }
    // Asks the host to prepare *instance*, when a resolver is installed.
    void resolve_type(PyObject* instance);

    // -- trace (trace.cpp) ------------------------------------------------
    //
    // A hook receives (event_name, member_name, detail) for every access the
    // bridge dispatches.  Disabled means one null check per event, which is why
    // the bridge can call it unconditionally.
    bool set_trace_hook(PyObject* hook);
    bool is_tracing() const { return trace_hook_ != nullptr; }
    // Borrowed, for reporting back to the host.
    PyObject* trace_hook() const { return trace_hook_; }
    void trace(TraceEvent event, const char* name, const char* detail);

    // The template used for a Python instance whose type has no installed
    // interface, created on first use.  The value layer owns what the object
    // does; the realm owns its lifetime.
    //
    // An ObjectTemplate, because a host object has no JavaScript constructor:
    // instances come from Python, and an instance template reached through a
    // function template could only be instantiated through that function.
    v8::Local<v8::ObjectTemplate> fallback_template(v8::Isolate* isolate);

    // Bind a Python type to the template its instances get in JavaScript.
    //
    // Without this, an instance built in Python and handed to JavaScript would be
    // a generic host object: it would not have the interface's prototype, so
    // `instanceof` would be false and not one of the interface's members would be
    // reachable.  With it, an object made by `new Event()` and an Event built in
    // Python are the same kind of thing to JavaScript.
    //
    // Returns false with a Python error set if another interface already claims
    // the type, because two claims would make which template an instance gets
    // depend on install order.
    bool register_python_type(PyTypeObject* type,
                              v8::Local<v8::FunctionTemplate> interface_template);

    // -- the interfaces this realm has installed (batch.cpp) ----------------
    //
    // A parent need not arrive in the same batch as its child.  A host that
    // builds interfaces on demand installs one at a time, and the second batch's
    // child has to inherit from a parent the first batch left behind -- the
    // constructor function V8 produced cannot be turned back into a template,
    // so the realm keeps the one it built.
    //
    // Registers the name, keeping the first template registered under it: two
    // batches that both install one interface are two descriptions of the same
    // interface, and the one already in the realm is the one in use.
    bool register_interface_template(const char* name,
                                     v8::Local<v8::FunctionTemplate> template_);
    // The template for one installed interface, or an empty handle.
    v8::Local<v8::FunctionTemplate> installed_interface(const char* name) const;
    // Whether this exact Python type has an interface installed here.
    //
    // The lookup walks the chain, so a derived type whose own interface is not
    // installed finds its base's -- which is the right answer for a type that
    // has no interface of its own, and the wrong one for a type that has one the
    // host has simply not installed yet.
    bool has_interface_for(PyTypeObject* type) const;

    // The instance template for a Python instance, or an empty handle when its
    // type has no interface.  A subclass of a registered type gets the registered
    // base's template, which is what makes a UIEvent an Event when only Event has
    // an interface installed.
    v8::Local<v8::ObjectTemplate> template_for_python_type(PyObject* instance);

    // The realm a context belongs to, or nullptr when the context is not ours.
    static Realm* from_context(v8::Local<v8::Context> context);

    // The Python wrapper for this realm, borrowed.  Handles keep their own
    // reference, so a handle can never outlive the realm object it came from.
    PyObject* python_object() const { return python_object_; }
    void set_python_object(PyObject* object) { python_object_ = object; }

    // A layer's own per-realm state, released when the realm closes.
    //
    // The bridge has to keep a batch alive -- its callables, its bindings -- for
    // as long as the realm can dispatch through them, and realm.h must not know
    // what a batch is.  One opaque slot with a deleter keeps the dependency
    // pointing the way it should.  Installing a second state releases the first.
    // Adopts a state the realm releases when it closes.
    //
    // A list rather than a single slot, because a realm is installed into more than
    // once: a lazy interface is installed the first time something reads its name,
    // and every earlier batch is still the data behind live V8 callbacks.  A single
    // slot would release the previous batch on the second install and leave those
    // callbacks pointing into freed memory.
    void own_state(void* state, void (*release)(void*));

    // Whether a lazy name is being materialized right now.
    //
    // A lazy factory that reads the name it is building would otherwise recurse:
    // the property is not cached while its own getter is still running, so the read
    // calls the getter again.  Materializing one name while materializing another is
    // fine and expected -- a factory that pulls in a parent is the normal case --
    // so this is asked about a name rather than about the realm.
    bool lazy_is_materializing(const char* name) const;
    void begin_lazy_materialization(const char* name);
    void end_lazy_materialization(const char* name);

    // Owns a wrapper record: a record dies when its JavaScript object is
    // collected or when the realm closes, whichever happens first.
    void attach(PythonOwner* record);
    void detach(PythonOwner* record);
    PythonOwner* owners() const { return owners_; }

    // Finish off wrapper records whose JavaScript object has been collected.
    //
    // A V8 weak callback may not release a Python reference: the reference can
    // run a finalizer, and running arbitrary Python with a live context on the
    // stack is exactly the re-entrancy the previous binding had to work around.
    // The callback therefore only marks the record, and the release happens
    // here, at a point the realm chooses.
    void reap_owners();

private:
    Realm() = default;
    ~Realm();

    Realm(const Realm&) = delete;
    Realm& operator=(const Realm&) = delete;

    // Deletes a realm created far enough to have an error pending, parking that
    // error across the delete: ~Realm clears the error state, and creation reports
    // its failures through it.
    static void destroy_after_failure(Realm* realm);

    // Releases every wrapper record, returning how many went away.
    std::size_t release_owners();
    // The path taken when the isolate cannot be entered: the Python references
    // still have to go, and the retained V8 handles deliberately do not.
    std::size_t discard_owners_without_isolate();

    Isolate* isolate_ = nullptr;
    v8::Global<v8::Context> context_;
    State state_ = State::kActive;
    // The name is copied, not borrowed: it comes from a Python string that is
    // released as soon as the call returns, and it outlives that by design.
    std::string name_storage_ = "realm";
    const char* name_ = "realm";
    PythonOwner* owners_ = nullptr;
    std::unique_ptr<ResourceRegistry> registry_;
    HandleSlot<v8::ObjectTemplate>* fallback_template_ = nullptr;
    // One Python type that has an interface installed, and the template its
    // instances get.  The template is ours to keep: nothing else holds it once
    // the interface has been turned into a function, and the instance template
    // cannot be recovered from the function.
    struct RegisteredType
    {
        PyTypeObject* type = nullptr;
        HandleSlot<v8::FunctionTemplate>* template_slot = nullptr;
    };
    std::vector<RegisteredType> registered_types_;
    // Every interface this realm has installed, by name.  Held as templates
    // because that is what inheritance needs, and only while the realm is open.
    struct InstalledInterface
    {
        std::string name;
        HandleSlot<v8::FunctionTemplate>* template_slot = nullptr;
    };
    std::vector<InstalledInterface> installed_interfaces_;
    // Types already found to have no interface, so a miss walks the MRO once per
    // type rather than once per crossing.
    std::vector<PyTypeObject*> unregistered_types_;
    // Whether this realm runs microtasks at all.  The queue itself is V8's: it
    // belongs to the context, and ownership never crosses into this binding, so
    // there is no teardown order to get wrong.
    bool microtasks_enabled_ = false;
    std::unique_ptr<TimeoutGuard> timeout_guard_;
    long long timeout_ms_ = 0;
    bool terminated_ = false;
    struct OwnedState
    {
        void* state = nullptr;
        void (*release)(void*) = nullptr;
    };
    std::vector<OwnedState> owned_states_;
    // Names whose lazy getters are on the stack, in the order they were entered.
    std::vector<std::string> lazy_materializing_;

    // Roots.  Their addresses are registered with registry_, so they must not
    // move: a Realm is heap allocated and never copied, and these are the only
    // Python references the realm owns.
    PyObject* uncaught_hook_ = nullptr;
    PyObject* rejection_hook_ = nullptr;
    PyObject* trace_hook_ = nullptr;
    PyObject* type_resolver_ = nullptr;
    // Borrowed: the Python wrapper owns the realm, not the other way round.
    PyObject* python_object_ = nullptr;
};

// Embedder data slots a realm's context carries.
//
//   0  External -> Realm
//
// Only slot 0 is the kernel's; the bridge and inspect layers take indexes above
// it so a single read of the context tells every layer whose it is.
constexpr int kRealmDataSlot = 0;
// Set once by Realm::create so the materialiser can recognise a plain object
// without asking the bridge whether an interface exists for it.
constexpr int kObjectPrototypeSlot = 1;

}  // namespace v8n

#endif
