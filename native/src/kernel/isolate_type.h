#ifndef V8N_KERNEL_ISOLATE_TYPE_H
#define V8N_KERNEL_ISOLATE_TYPE_H

#include <Python.h>

namespace v8n
{

// Publish v8n.Isolate.  Called by the module's type registry, which owns
// construction order.
int register_isolate_type(PyObject* module);

// The kernel isolate behind a v8n.Isolate, or nullptr when the argument is not
// a live one.  Borrowed: the wrapper keeps owning it.
class Isolate;
Isolate* isolate_of(PyObject* object);

}  // namespace v8n

#endif
