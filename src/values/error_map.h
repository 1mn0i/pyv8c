#ifndef PYV8_ERROR_MAP_H
#define PYV8_ERROR_MAP_H

#include <v8.h>

namespace error_map {

// The V8 error a Python exception name maps to, or an empty object when the
// binding knows no mapping for it.  One table answers both directions, so a name
// cannot be spelled one way into JavaScript and another way back.
//
// *explicit_name* says the Python class named itself with ``__v8_error_name__``:
// the bare JavaScript spellings are accepted only then, which is what keeps a
// host class that happens to be called ``TypeError`` from being mapped by
// accident.
v8::Local<v8::Object> js_error_for(
    v8::Isolate* isolate, const char* name, const char* message, bool explicit_name);

}  // namespace error_map

#endif
