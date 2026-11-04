#ifndef PYV8_ISOLATE_ENTRY_H
#define PYV8_ISOLATE_ENTRY_H
#include "bridge_base.h"
#include "isolate_scope.h"

// Isolate-only entry, for initialization, teardown and engine-global work.
// Ordinary Context operations use ExecutionScope and its post-wait validation.
#define IN_ISOLATE(state)                            \
    IsolateLockScope isolate_lock_scope(state);       \
    if (!isolate_lock_scope.entered()) throw PythonEntryFailure{}; \
    v8::Isolate* isolate = isolate_lock_scope.isolate(); \
    USING_V8

#define ESCAPING_IN_ISOLATE(state)                   \
    IsolateLockScope isolate_lock_scope(state);       \
    if (!isolate_lock_scope.entered()) throw PythonEntryFailure{}; \
    v8::Isolate* isolate = isolate_lock_scope.isolate(); \
    ESCAPING_V8

#define USING_V8 \
    v8::HandleScope hs(isolate)

#define ESCAPING_V8 \
    v8::EscapableHandleScope hs(isolate)

#define IN_CONTEXT(ctx)               \
    v8::Local<v8::Context> context = ctx; \
    v8::Context::Scope cs(context);


#endif
