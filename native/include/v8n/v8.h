#ifndef V8N_V8_H
#define V8N_V8_H

// The single entry point for V8's headers.
//
// On Windows, Python's pyconfig.h defines COMPILER as a version string:
//
//     #define COMPILER ("[MSC v.1944 64 bit (AMD64)]")
//
// and V8's v8-unwinder.h uses COMPILER as an enumerator of v8::StateTag:
//
//     enum StateTag : uint16_t { JS, GC, PARSER, BYTECODE_COMPILER, COMPILER, ... };
//
// Including both without this undef turns v8-unwinder.h into a syntax error
// storm hundreds of lines away from the cause.  Every header and translation
// unit therefore reaches V8 through here instead of including <v8.h> directly,
// so the workaround cannot be forgotten in one file out of thirty.

#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#endif

#include <v8.h>

#endif
