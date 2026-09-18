#ifndef V8N_HANDLE_SLOT_H
#define V8N_HANDLE_SLOT_H

#include "v8n/v8.h"

#include "v8n/kernel.h"

namespace v8n
{

// The link that lets an isolate find every retained handle without knowing the
// handle's type, so teardown can release them before V8 goes away.
//
// Slots derive from this node rather than containing it, so a node pointer can
// be turned back into its slot with a single static_cast.
struct HandleSlotNode
{
    HandleSlotNode* next = nullptr;
    HandleSlotNode* previous = nullptr;
    Isolate* isolate = nullptr;
    // Set by the derived slot: releases its V8 handle.  Unlinking and counting
    // are the isolate's business.
    void (*release)(HandleSlotNode*) = nullptr;
};

// A retained V8 handle.
//
// The previous binding kept a Global<T> in each Python wrapper and paired
// retain_v8_handle()/release_v8_handle() by hand in six files.  A slot does the
// counting in its constructor and destructor, so the handle and the count
// cannot disagree, and it registers itself with the isolate so teardown can
// release handles a host forgot about.
//
// A slot is stored inside a Python object's C storage and is never moved, so
// moving is deleted rather than implemented: list membership would otherwise
// have to be repaired on every transfer.
template <typename T>
class HandleSlot : public HandleSlotNode
{
public:
    HandleSlot() = default;

    HandleSlot(Isolate& isolate, v8::Local<T> value) { emplace(isolate, value); }

    ~HandleSlot() { reset(); }

    HandleSlot(const HandleSlot&) = delete;
    HandleSlot& operator=(const HandleSlot&) = delete;
    HandleSlot(HandleSlot&&) = delete;
    HandleSlot& operator=(HandleSlot&&) = delete;

    // Retain value.  An empty value leaves the slot empty, which is what a
    // caller that could not produce a handle wants.
    bool emplace(Isolate& isolate, v8::Local<T> value)
    {
        reset();
        if (value.IsEmpty())
            return false;
        handle_.Reset(isolate.v8(), value);
        release = &HandleSlot::release_handle;
        isolate.attach_slot(*this);
        return true;
    }

    void reset()
    {
        if (isolate == nullptr)
            return;
        Isolate* owner = isolate;
        owner->detach_slot(*this);
        handle_.Reset();
        release = nullptr;
    }

    bool empty() const { return isolate == nullptr; }

    // A retained handle only means anything to the isolate that produced it.
    v8::Local<T> get(v8::Isolate* target) const
    {
        if (isolate == nullptr || isolate->v8() != target)
            return v8::Local<T>();
        return handle_.Get(target);
    }

private:
    static void release_handle(HandleSlotNode* node)
    {
        static_cast<HandleSlot*>(node)->handle_.Reset();
    }

    v8::Global<T> handle_;
};

}  // namespace v8n

#endif
