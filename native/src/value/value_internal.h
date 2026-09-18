#ifndef V8N_VALUE_INTERNAL_H
#define V8N_VALUE_INTERNAL_H

#include <Python.h>

#include "v8n/kernel.h"

namespace v8n::value
{

// The template used for a Python instance whose type has no installed
// interface.
//
// An ObjectTemplate rather than a FunctionTemplate, on purpose: an instance
// template that hangs off a function template can only be instantiated through
// that function, and a function template with no callback dereferences a null
// one.  A host object has no JavaScript constructor at all -- instances come
// from Python -- so there is nothing for a function template to do.
//
// Named property access reads and writes Python attributes, which is what makes
// the wrapper useful at all.  A type with an interface gets that interface's
// template from the bridge layer instead, and both carry the same owner field,
// so recognising a Python-backed object does not care which one produced it.
v8::Local<v8::ObjectTemplate> make_fallback_template(v8::Isolate* isolate);

// The marker objects that stand for JavaScript's null and undefined.
//
// They are plain Python singletons rather than retained handles: null and
// undefined are primitives, so a conversion only has to recognise the singleton
// and produce the primitive.  Keeping them alive costs nothing and avoids a
// handle per isolate for a value that has no identity in V8.
PyObject* null_singleton();
PyObject* undefined_singleton();
bool is_null_singleton(PyObject* value);
bool is_undefined_singleton(PyObject* value);

// v8n.Null, v8n.Undefined and their types.
int register_primitives(PyObject* module);

// v8n.Value.
int register_handle_type(PyObject* module);

// The handle type object, used to recognise a wrapper without an isinstance
// check that would walk the type's MRO.
extern PyTypeObject* value_type;

}  // namespace v8n::value

#endif
