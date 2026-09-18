#include "abi3/abi3_shim.h"

namespace v8n::abi3
{
namespace
{
// types.FunctionType and types.MethodType, resolved once at module execution.
// The limited API exposes no PyFunction_Type / PyMethod_Type, and the concrete
// structs are invisible, so the only portable test is an instance check
// against the runtime classes.
PyObject* function_type = nullptr;
PyObject* method_type = nullptr;

bool cache_type(PyObject* types_module, const char* name, PyObject** target)
{
    *target = PyObject_GetAttrString(types_module, name);
    return *target != nullptr;
}
}

int
CacheCallableTypes()
{
    if (function_type != nullptr && method_type != nullptr)
        return 0;

    PyObject* types_module = PyImport_ImportModule("types");
    if (types_module == nullptr)
        return -1;

    bool cached = cache_type(types_module, "FunctionType", &function_type) &&
                  cache_type(types_module, "MethodType", &method_type);
    Py_DECREF(types_module);
    return cached ? 0 : -1;
}

int
IsFunction(PyObject* object)
{
    if (function_type == nullptr)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "v8n callable types were not cached during module init");
        return -1;
    }
    return PyObject_IsInstance(object, function_type);
}

int
IsMethod(PyObject* object)
{
    if (method_type == nullptr)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "v8n callable types were not cached during module init");
        return -1;
    }
    return PyObject_IsInstance(object, method_type);
}

PyObject*
NewMethod(PyObject* callable, PyObject* receiver)
{
    if (method_type == nullptr)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "v8n callable types were not cached during module init");
        return nullptr;
    }
    return PyObject_CallFunctionObjArgs(method_type, callable, receiver, nullptr);
}

}  // namespace v8n::abi3
