#include "abi3.h"
PyObject* g_func_type = nullptr;
PyObject* g_method_type = nullptr;

int 
cache_FunctionType_init() 
{

    PyOwnedRef types_module(PyImport_ImportModule("types"));
    if (!types_module.get()) {
        return -1;
    }

    g_func_type = PyObject_GetAttrString(types_module.get(), "FunctionType");
    if (!g_func_type) {
        return -1;
    }
    g_method_type = PyObject_GetAttrString(types_module.get(), "MethodType");
    if (!g_method_type) {
        return -1;
    }

    return 0;
}


int 
PyFunction_Check_abi3(PyObject* op) {
    return PyObject_IsInstance(op, g_func_type);
}

int
PyMethod_Check_abi3(PyObject* op) {
    return PyObject_IsInstance(op, g_method_type);
}
