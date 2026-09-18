#ifndef V8N_KERNEL_H
#define V8N_KERNEL_H

// The layer that owns V8 itself: one process-wide platform, and isolates that
// belong to the thread that created them.  Nothing here knows about realms,
// interfaces or Python types; it only decides who may talk to V8 and when V8
// exists.
//
// The previous binding kept a process-wide isolate that any thread could enter
// under a v8::Locker, which forced every entry site to interleave the GIL with
// the locker.  This kernel removes the possibility instead of coordinating it.

#include "v8n/v8.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <thread>

namespace v8n
{

// Process-wide V8 platform.
//
// initialize() is idempotent.  shutdown() disposes V8 for the whole process and
// refuses while any isolate is open, so the failure surfaces where the mistake
// is instead of as a crash inside V8.
class Platform
{
public:
    // icudtl_path may be null to use the ICU data linked into V8.  flags is a
    // sequence of strings handed to V8::SetFlagsFromString, and can only be
    // supplied before the platform starts.
    static bool initialize(PyObject* icudtl_path, PyObject* flags);
    static bool shutdown();

    static bool is_initialized();
    static bool is_disposed();
    static v8::Platform* get();

private:
    Platform() = delete;
};

// The V8 version this extension was compiled and linked against.
const char* version();

// Strings the object model asks for on nearly every conversion.  Interning them
// once per isolate replaces a UTF-8 hash per lookup with a local handle, and
// gives the isolate a fixed, checkable number of owned handles.
enum class IsolateString : std::uint8_t
{
    Constructor,
    Prototype,
    Message,
    Length,
    Name,
    ToStringTag,
    // Marks an object that is backed by a Python instance.  A distinct,
    // greppable value rather than a phrase that reads like an accident.
    PythonObject,

    Count,
};

struct HandleSlotNode;
template <typename T>
class HandleSlot;

// One isolate, belonging to the thread that created it.
//
// The owner thread takes the v8::Locker once, when the isolate is created, and
// holds it for the isolate's lifetime.  Entering the isolate from that thread is
// therefore just Isolate::Scope -- there is no lock to acquire and nothing to
// hand over, which is why the GIL juggling the previous binding needed at every
// entry point disappears.  Any other thread is rejected at the boundary.
class Isolate
{
public:
    // Returns nullptr with a Python error set when V8 is not initialized or the
    // isolate could not be created.
    static Isolate* create();

    // Destroys an isolate the caller owns.  Only legal on the owner thread:
    // releasing V8's lock from anywhere else is not something V8 allows.
    static void destroy(Isolate* isolate);

    // Destroys the isolate now when the caller owns it, and otherwise leaves it
    // for the owner thread to reclaim.
    //
    // This exists because a Python wrapper can be deallocated on any thread: a
    // reference count reaching zero has nothing to do with the isolate's
    // threading rule.  A dealloc cannot report the problem, and destroying V8
    // from the wrong thread would trip a V8 check, so the isolate is parked
    // instead and the owner thread finishes the job later.
    static void abandon(Isolate* isolate);

    // Destroys every abandoned isolate owned by the calling thread and returns
    // how many went away.  Called at natural points, notably before a new
    // isolate is created and before the platform shuts down.
    static std::size_t reclaim();

    // The record for a V8 isolate, or nullptr for a foreign one.
    static Isolate* from(v8::Isolate* isolate);
    // The isolate currently entered on this thread, or nullptr.
    static Isolate* current();
    // How many isolates are open in this process.
    static std::size_t live_count();

    ~Isolate();

    Isolate(const Isolate&) = delete;
    Isolate& operator=(const Isolate&) = delete;

    v8::Isolate* v8() const { return isolate_; }
    v8::ArrayBuffer::Allocator* allocator() const { return allocator_.get(); }

    // A printable identity for the owning thread, for error messages and tests.
    unsigned long long owner_thread() const { return owner_thread_; }
    bool is_owner_thread() const;

    // Returns false with a Python error set when the caller is not the owner.
    bool require_owner_thread(const char* operation) const;

    // Number of retained handles.  Equal to the number of interned strings right
    // after creation, and back to zero after teardown.
    std::size_t live_handles() const { return live_handles_; }

    // A string from the per-isolate table.  Only valid on the owner thread.
    v8::Local<v8::String> interned(IsolateString id) const;

private:
    Isolate() = default;

    template <typename T>
    friend class HandleSlot;

    // What an isolate stores in V8's embedder data slot 0 so a V8 isolate can
    // be mapped back to its record.  V8 zero-initializes that slot, and nothing
    // else in this process writes it, so a foreign isolate reads as null.
    struct Slot
    {
        static constexpr std::uint32_t kMagic = 0x76386E31u;  // 'v8n1'

        std::uint32_t magic = 0;
        Isolate* self = nullptr;
    };

    void attach_slot(HandleSlotNode& node);
    void detach_slot(HandleSlotNode& node);
    // Release and unlink every retained handle.  Runs before V8 is disposed so
    // no Global<T> outlives the isolate it points into.
    void detach_all_slots();

    bool intern_strings();

    std::unique_ptr<v8::ArrayBuffer::Allocator> allocator_;
    std::unique_ptr<v8::Locker> locker_;
    v8::Isolate* isolate_ = nullptr;
    std::thread::id owner_id_;
    unsigned long long owner_thread_ = 0;
    std::size_t live_handles_ = 0;
    HandleSlotNode* handle_head_ = nullptr;
    Slot slot_;
    std::unique_ptr<HandleSlot<v8::String>[]> interned_;
};

// Enters an isolate for the duration of a scope.
//
// The owner thread already holds the Locker, so entering only makes this
// isolate the thread's current one.  A failed entry leaves the scope unusable
// and the Python error already set, which is what a caller checks with ok().
class IsolateEntry
{
public:
    explicit IsolateEntry(Isolate& isolate);
    ~IsolateEntry();

    IsolateEntry(const IsolateEntry&) = delete;
    IsolateEntry& operator=(const IsolateEntry&) = delete;

    bool ok() const { return scope_ != nullptr; }
    v8::Isolate* v8() const { return isolate_ == nullptr ? nullptr : isolate_->v8(); }
    v8::Local<v8::Context> context();

private:
    Isolate* isolate_ = nullptr;
    std::unique_ptr<v8::Isolate::Scope> scope_;
};

}  // namespace v8n

#endif
