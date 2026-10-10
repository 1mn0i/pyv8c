#ifndef PYV8ENV_H
#define PYV8ENV_H

#ifdef _WIN32
// v8.h(1680): error C2059: syntax error: '('
#undef COMPILER
#define NO_RETURN __declspec(noreturn)
#else
#define NO_RETURN __attribute__((noreturn))
#endif


#include <v8.h>
#include "v8_compat.h"
#include "polyfill.h"
#include "crossing.h"
#include "js_exception.h"
#include "error_family.h"
#include "abi3.h"
#include "gil_scope.h"
#include "jsnull.h"
#include "jsundefined.h"
#include "runtime.h"
#include "isolate_scope.h"
#include "isolate_state.h"
#include "execution.h"
#include "callback_boundary.h"

#define JSTR(str) v8::String::NewFromUtf8(isolate, str).ToLocalChecked()

#define V8String(isolate, str) v8::String::NewFromUtf8(isolate, str).ToLocalChecked()

#define IN_ISOLATE(state)                            \
    IsolateLockScope isolate_lock_scope(state);       \
    v8::Isolate* isolate = isolate_lock_scope.isolate(); \
    USING_V8

#define ESCAPING_IN_ISOLATE(state)                   \
    IsolateLockScope isolate_lock_scope(state);       \
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
