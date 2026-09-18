#ifndef V8N_MODULE_REGISTRY_H
#define V8N_MODULE_REGISTRY_H

#include <Python.h>

namespace v8n::module
{

// Publish a heap type under `name`.
//
// PyType_FromSpec hands back a new reference.  The module takes its own, and
// the caller keeps the one it was given, so the type stays alive even if an
// embedder drops the module attribute.  Returns false with a Python error set
// when the name is already present.
bool publish_type(PyObject* module, const char* name, PyTypeObject* type);

// Register every type this extension owns, in dependency order: a type built
// with PyType_FromSpecWithBases needs its base to exist first.  Called once,
// during module execution.
int register_types(PyObject* module);

// V8 is process-global, so the extension refuses to run anywhere but the main
// interpreter.  Returns false with ImportError set anywhere else.
bool require_main_interpreter();

}  // namespace v8n::module

#endif
