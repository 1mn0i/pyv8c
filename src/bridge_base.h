#ifndef PYV8_BRIDGE_BASE_H
#define PYV8_BRIDGE_BASE_H
#include <Python.h>
#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#else
#ifndef NO_RETURN
#define NO_RETURN __attribute__((noreturn))
#endif
#endif
#include <v8.h>
#include "v8_compat.h"
#include "polyfill.h"
#include "abi3.h"
#include "gil_scope.h"
#include "callback_boundary.h"
#define JSTR(str) v8::String::NewFromUtf8(isolate, str).ToLocalChecked()
#define V8String(isolate, str) v8::String::NewFromUtf8(isolate, str).ToLocalChecked()
#endif
