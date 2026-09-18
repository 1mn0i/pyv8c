#include "v8n/kernel.h"

#include "boundary/guard.h"
#include "kernel/handle_slot.h"

#include <atomic>
#include <mutex>
#include <thread>
#include <vector>

namespace v8n
{
namespace
{
std::atomic<std::size_t> isolate_count{0};

// Isolates whose Python wrapper was deallocated on a thread that does not own
// them.  Only their owner thread can finish destroying them, so they wait here
// until it comes back or the process says it is shutting down.
std::mutex abandoned_mutex;
std::vector<Isolate*> abandoned;

// The embedder data slot an isolate uses to point back at its record; see
// Isolate::Slot.
constexpr std::uint32_t kIsolateDataSlot = 0;

unsigned long long thread_identity()
{
    return static_cast<unsigned long long>(
        std::hash<std::thread::id>{}(std::this_thread::get_id()));
}

// The strings the object model reaches for on nearly every conversion.
constexpr const char* kInternedStrings[] = {
    "constructor",
    "prototype",
    "message",
    "length",
    "name",
    "toStringTag",
    "v8n:python-object",
};

static_assert(
    sizeof(kInternedStrings) / sizeof(kInternedStrings[0]) ==
        static_cast<std::size_t>(IsolateString::Count),
    "the interned string table and IsolateString must stay in step");
}

bool
Isolate::is_owner_thread() const
{
    return std::this_thread::get_id() == owner_id_;
}

bool
Isolate::require_owner_thread(const char* operation) const
{
    if (isolate_ == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: the isolate is closed", operation);
        return false;
    }
    if (!is_owner_thread())
    {
        PyErr_Format(PyExc_RuntimeError,
                     "%s must run on the thread that created this isolate "
                     "(owner thread %llu, called from %llu)",
                     operation, owner_thread_, thread_identity());
        return false;
    }
    return true;
}

void
Isolate::attach_slot(HandleSlotNode& node)
{
    node.isolate = this;
    node.previous = nullptr;
    node.next = handle_head_;
    if (handle_head_ != nullptr)
        handle_head_->previous = &node;
    handle_head_ = &node;
    ++live_handles_;
}

void
Isolate::detach_slot(HandleSlotNode& node)
{
    if (node.previous != nullptr)
        node.previous->next = node.next;
    else if (handle_head_ == &node)
        handle_head_ = node.next;
    if (node.next != nullptr)
        node.next->previous = node.previous;

    node.next = nullptr;
    node.previous = nullptr;
    node.isolate = nullptr;
    if (live_handles_ > 0)
        --live_handles_;
}

void
Isolate::detach_all_slots()
{
    HandleSlotNode* node = handle_head_;
    handle_head_ = nullptr;
    live_handles_ = 0;

    while (node != nullptr)
    {
        HandleSlotNode* next = node->next;
        node->next = nullptr;
        node->previous = nullptr;
        node->isolate = nullptr;
        if (node->release != nullptr)
            node->release(node);
        node->release = nullptr;
        node = next;
    }
}

bool
Isolate::intern_strings()
{
    constexpr std::size_t count = static_cast<std::size_t>(IsolateString::Count);
    interned_.reset(new HandleSlot<v8::String>[count]);

    for (std::size_t index = 0; index < count; ++index)
    {
        v8::Local<v8::String> value;
        if (!v8::String::NewFromUtf8(isolate_, kInternedStrings[index],
                                     v8::NewStringType::kInternalized)
                 .ToLocal(&value))
        {
            raise(PyExc_RuntimeError, "failed to intern the string \"%s\"",
                  kInternedStrings[index]);
            return false;
        }
        interned_[index].emplace(*this, value);
    }
    return true;
}

v8::Local<v8::String>
Isolate::interned(IsolateString id) const
{
    const std::size_t index = static_cast<std::size_t>(id);
    if (interned_ == nullptr || index >= static_cast<std::size_t>(IsolateString::Count))
        return v8::Local<v8::String>();
    return interned_[index].get(isolate_);
}

Isolate*
Isolate::create()
{
    if (!Platform::is_initialized())
    {
        raise(PyExc_RuntimeError, "the V8 platform is not initialized");
        return nullptr;
    }

    // Finish anything this thread left behind before adding to it, so a host
    // that creates and drops isolates in a loop never accumulates them.
    reclaim();

    Isolate* self = new (std::nothrow) Isolate();
    if (self == nullptr)
    {
        PyErr_NoMemory();
        return nullptr;
    }

    self->allocator_.reset(v8::ArrayBuffer::Allocator::NewDefaultAllocator());
    if (!self->allocator_)
    {
        raise(PyExc_RuntimeError, "failed to create the V8 ArrayBuffer allocator");
        delete self;
        return nullptr;
    }

    v8::Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = self->allocator_.get();
    self->isolate_ = v8::Isolate::New(create_params);
    if (self->isolate_ == nullptr)
    {
        raise(PyExc_RuntimeError, "failed to create the V8 isolate");
        delete self;
        return nullptr;
    }

    self->owner_id_ = std::this_thread::get_id();
    self->owner_thread_ = thread_identity();
    self->slot_.magic = Slot::kMagic;
    self->slot_.self = self;
    self->isolate_->SetData(kIsolateDataSlot, &self->slot_);

    // Counted from here on: everything below can fail, and the destructor
    // unwinds by decrementing, which would underflow an uncounted isolate.
    ++isolate_count;

    // The Locker is taken once, here, and released in the destructor.  Because
    // only this thread ever enters the isolate, holding it is what makes every
    // later entry a plain Isolate::Scope.
    self->locker_ = std::make_unique<v8::Locker>(self->isolate_);

    {
        IsolateEntry entry(*self);
        v8::HandleScope handle_scope(self->isolate_);
        if (!entry.ok() || !self->intern_strings())
        {
            delete self;
            return nullptr;
        }
    }

    self->isolate_->SetCaptureStackTraceForUncaughtExceptions(
        true, 100,
        static_cast<v8::StackTrace::StackTraceOptions>(
            v8::StackTrace::kOverview | v8::StackTrace::kScriptId));

    return self;
}

void
Isolate::destroy(Isolate* isolate)
{
    delete isolate;
}

void
Isolate::abandon(Isolate* isolate)
{
    if (isolate == nullptr)
        return;
    if (isolate->is_owner_thread())
    {
        delete isolate;
        return;
    }

    std::lock_guard<std::mutex> guard(abandoned_mutex);
    abandoned.push_back(isolate);
}

std::size_t
Isolate::reclaim()
{
    std::vector<Isolate*> ready;
    {
        std::lock_guard<std::mutex> guard(abandoned_mutex);
        for (auto item = abandoned.begin(); item != abandoned.end();)
        {
            if ((*item)->is_owner_thread())
            {
                ready.push_back(*item);
                item = abandoned.erase(item);
            }
            else
            {
                ++item;
            }
        }
    }

    for (Isolate* isolate : ready)
        delete isolate;
    return ready.size();
}

Isolate*
Isolate::from(v8::Isolate* isolate)
{
    if (isolate == nullptr)
        return nullptr;
    auto* slot = static_cast<Slot*>(isolate->GetData(kIsolateDataSlot));
    if (slot == nullptr || slot->magic != Slot::kMagic)
        return nullptr;
    return slot->self;
}

Isolate*
Isolate::current()
{
    return from(v8::Isolate::GetCurrent());
}

std::size_t
Isolate::live_count()
{
    return isolate_count.load();
}

Isolate::~Isolate()
{
    if (isolate_ == nullptr)
        return;

    {
        // Releasing a Global<T> needs the isolate alive, so every slot is
        // released before V8 is disposed.  This is what makes a host that
        // forgot to close a realm a leak of Python objects rather than a crash.
        IsolateEntry entry(*this);
        v8::HandleScope handle_scope(isolate_);
        detach_all_slots();
    }

    // The Locker has to be gone before Dispose: V8 refuses to dispose an
    // isolate the current thread still holds.
    interned_.reset();
    locker_.reset();
    isolate_->Dispose();
    isolate_ = nullptr;

    allocator_.reset();
    --isolate_count;
}

IsolateEntry::IsolateEntry(Isolate& isolate)
{
    if (!isolate.require_owner_thread("entering the isolate"))
        return;
    isolate_ = &isolate;
    scope_ = std::make_unique<v8::Isolate::Scope>(isolate.v8());
}

IsolateEntry::~IsolateEntry() = default;

v8::Local<v8::Context>
IsolateEntry::context()
{
    if (isolate_ == nullptr)
        return v8::Local<v8::Context>();
    return isolate_->v8()->GetCurrentContext();
}

}  // namespace v8n
