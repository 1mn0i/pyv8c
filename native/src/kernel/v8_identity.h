#ifndef V8N_V8_IDENTITY_H
#define V8N_V8_IDENTITY_H

// Checks that the extension and the V8 library it links agree about the parts
// V8 does not expose in a header.
//
// Two of them can silently mismatch and produce a crash rather than an error:
//
//   * ICU data.  A wheel built against one V8 and loaded next to another V8's
//     icudtl.dat fails deep inside ICU, so the file's SHA-256 is baked into the
//     extension at build time and compared before ICU is initialised.
//   * v8::MicrotaskQueue's layout.  It follows the V8_CPPGC_MICROTASK_QUEUE
//     macro, which V8 passes on its own compile command line and ships in no
//     public header.  An embedder built without it disagrees with the library
//     about the class and faults on every virtual call into a queue, so the
//     answer is compiled in and callers must refuse to enqueue when it is off.

#include <Python.h>

namespace v8n
{

// Verify icudtl_path against the SHA-256 recorded at build time.  Returns false
// with a Python error set on mismatch or on a read failure.  When the extension
// was built without a recorded digest the check is skipped.
bool verify_icu_data(PyObject* icudtl_path);

// Whether this build can use v8::MicrotaskQueue at all.
bool microtask_queue_supported();

// The digest recorded at build time, or an empty string.
const char* expected_icu_digest();

}  // namespace v8n

#endif
