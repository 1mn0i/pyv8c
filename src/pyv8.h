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
#include "polyfill.h"
#include "exception.h"
#include "kappa.h"
#include "abi3.h"
#include "jsnull.h"
#include "jsundefined.h"

using namespace v8;

extern Isolate *isolate;

#define HOOKHANDLE "faker"

#define STRING_BUFFER_SIZE 512
static uint16_t string_buffer[STRING_BUFFER_SIZE] = {};

#define NORETURN __attribute__ ((noreturn))

#define JSTR(str) String::NewFromUtf8(isolate, str).ToLocalChecked()

#define V8String(isolate, str) String::NewFromUtf8(isolate, str).ToLocalChecked()

#define PYDUMP(obj) printf("%s\n", PyUnicode_AsUTF8_abi3(obj));


#define JSDUMP(obj)                   \
    {                                 \
        String::Utf8Value value(obj); \
        printf("%s\n", *value);       \
    }

#define IN_V8                   \
    Locker locker(isolate);     \
    Isolate::Scope is(isolate); \
    USING_V8

#define ESCAPING_IN_V8          \
    Locker locker(isolate);     \
    Isolate::Scope is(isolate); \
    ESCAPING_V8

#define USING_V8 \
    HandleScope hs(isolate)

#define ESCAPING_V8 \
    EscapableHandleScope hs(isolate)

#define IN_CONTEXT(ctx)           \
    Local<Context> context = ctx; \
    Context::Scope cs(context);

#endif
