#ifndef V8N_REALM_TYPE_H
#define V8N_REALM_TYPE_H

#include <Python.h>

namespace v8n
{

// Publish v8n.Realm.  Called by the module's type registry, which owns
// construction order.
int register_realm_type(PyObject* module);

}  // namespace v8n

#endif
