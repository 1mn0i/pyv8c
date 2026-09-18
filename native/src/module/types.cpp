#include "module/registry.h"

#include "boundary/guard.h"
#include "kernel/isolate_type.h"
#include "realm/realm_type.h"
#include "v8n/boundary.h"
#include "v8n/value.h"

namespace v8n::module
{

bool
publish_type(PyObject* module, const char* name, PyTypeObject* type)
{
    if (type == nullptr)
        return false;
    // AddObjectRef adds its own reference and leaves ours alone.
    if (PyModule_AddObjectRef(module, name, reinterpret_cast<PyObject*>(type)) < 0)
        return false;
    return true;
}

int
register_types(PyObject* module)
{
    // Types arrive with the layers that own them, in this order:
    //
    //   kernel  -- Isolate
    //   realm   -- Realm, and the resource records it hands to Python
    //   value   -- Handle and its specialisations, the primitives
    //   bridge  -- the IDL descriptor objects the batch API consumes
    //
    // Each layer appends its registrations here, in dependency order, so there
    // is exactly one place that decides what exists before what.
    if (register_isolate_type(module) < 0)
        return -1;
    if (register_value_types(module) < 0)
        return -1;
    if (register_realm_type(module) < 0)
        return -1;
    return 0;
}

bool
require_main_interpreter()
{
#ifdef Py_GIL_DISABLED
    PyErr_SetString(
        PyExc_ImportError,
        "v8n does not support free-threaded CPython builds");
    return false;
#else
    PyThreadState* thread_state = PyThreadState_Get();
    PyInterpreterState* interpreter = PyThreadState_GetInterpreter(thread_state);
    const int64_t interpreter_id = PyInterpreterState_GetID(interpreter);
    if (interpreter_id < 0)
        return false;
    if (interpreter_id != 0)
    {
        PyErr_SetString(
            PyExc_ImportError,
            "v8n only supports the main Python interpreter because its V8 runtime "
            "is process-global");
        return false;
    }
    return true;
#endif
}

}  // namespace v8n::module
