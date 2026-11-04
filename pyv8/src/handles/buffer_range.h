#ifndef PYV8_BUFFER_RANGE_H
#define PYV8_BUFFER_RANGE_H
#include "bridge_base.h"
#include <memory>

// Own the backing store throughout range use. Call only inside a guarded entry;
// ranges are snapshots, never a cache across JavaScript execution or detach.
struct BufferRange {
    std::shared_ptr<v8::BackingStore> store;
    size_t offset = 0;
    size_t length = 0;
    bool is_buffer = false;

    const char* data() const {
        if (!store || !store->Data() || length == 0) return nullptr;
        return static_cast<const char*>(store->Data()) + offset;
    }
    bool resizable() const { return store && store->IsResizableByUserJavaScript(); }
};

inline BufferRange buffer_range(v8::Local<v8::Object> object) {
    BufferRange range;
    if (object->IsArrayBuffer()) {
        range.is_buffer = true;
        auto buffer = object.As<v8::ArrayBuffer>();
        if (!buffer->WasDetached()) range.store = buffer->GetBackingStore();
    } else if (object->IsSharedArrayBuffer()) {
        range.is_buffer = true;
        range.store = object.As<v8::SharedArrayBuffer>()->GetBackingStore();
    } else if (object->IsArrayBufferView()) {
        range.is_buffer = true;
        auto view = object.As<v8::ArrayBufferView>();
        auto buffer = view->Buffer();
        range.store = buffer->GetBackingStore();
        range.offset = view->ByteOffset();
        range.length = view->ByteLength();
        if (buffer->WasDetached()) { range.offset = 0; range.length = 0; }
        if (range.store && (range.offset > range.store->ByteLength() ||
            range.length > range.store->ByteLength() - range.offset)) {
            range.offset = 0;
            range.length = 0;
        }
        return range;
    }
    if (range.store) range.length = range.store->ByteLength();
    return range;
}
#endif
