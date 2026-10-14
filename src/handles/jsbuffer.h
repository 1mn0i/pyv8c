#ifndef PYV8_JSBUFFER_H
#define PYV8_JSBUFFER_H

// bridge_base.h first: it carries the MSVC shims V8's public headers expect.
#include "bridge_base.h"

#include "jsobject.h"

#include <memory>


//: The bytes of a JavaScript buffer, exposed through Python's buffer protocol without a
//: copy.  The handle keeps the buffer alive the way any other handle does, and the backing
//: store is held separately because V8 keeps it alive past a detach and past the handle
//: (v8-array-buffer.h:379-387: the embedder manages the storage through that pointer and
//: no other way).  Chromium models the same relationship in ArrayBufferContents, whose only
//: member is that shared pointer (array_buffer_contents.h:175).
typedef struct {
    js_object base;
    std::shared_ptr<v8::BackingStore>* store;
} js_buffer;

extern PyTypeObject* js_buffer_type;
int js_buffer_type_init();

//: ``value.buffer_view()``: the buffer object above, or a TypeError when the value is
//: neither a buffer nor a view -- what ``bytes(value)`` would refuse for the same reason.
PyObject* js_object_buffer(js_object* self, PyObject* unused);

#endif
