#ifndef PYV8_V8_HANDLE_SLOT_H
#define PYV8_V8_HANDLE_SLOT_H

#include <v8.h>

#include "isolate_state.h"

// A persistent handle that counts itself.  `live_v8_handles_` is what decides
// whether an isolate may be closed, so a handle has to be counted in the same
// step it is created and released in the same step it is reset -- hand-written
// pairs are how a handle went uncounted.
template <class T>
class V8HandleSlot
{
public:
    V8HandleSlot() = default;
    ~V8HandleSlot() { Reset(); }

    V8HandleSlot(const V8HandleSlot&) = delete;
    V8HandleSlot& operator=(const V8HandleSlot&) = delete;

    void Set(IsolateState* state, v8::Isolate* isolate, v8::Local<T> value)
    {
        Reset();
        if (state == nullptr || value.IsEmpty())
            return;
        handle_.Reset(isolate, value);
        state_ = state;
        state_->retain_v8_handle();
    }

    void Reset()
    {
        if (handle_.IsEmpty())
            return;
        handle_.Reset();
        if (state_ != nullptr)
        {
            state_->release_v8_handle();
            state_ = nullptr;
        }
    }

    v8::Local<T> Get(v8::Isolate* isolate) const { return handle_.Get(isolate); }
    bool IsEmpty() const { return handle_.IsEmpty(); }

    // For the one thing a slot cannot express: a weak registration.
    v8::Persistent<T>& persistent() { return handle_; }

private:
    v8::Persistent<T> handle_;
    IsolateState* state_ = nullptr;
};

#endif
