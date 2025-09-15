#include "abi3.h"

//PyObject* g_func_type = NULL;

int 
cache_FunctionType_init() 
{

    PyObject* types_module = PyImport_ImportModule("types");
    if (!types_module) {
        return -1;
    }

    g_func_type = PyObject_GetAttrString(types_module, "FunctionType");
    if (!g_func_type) {
        Py_DECREF(types_module);
        return -1;
    }
    g_method_type = PyObject_GetAttrString(types_module, "MethodType");
    if (!g_method_type) {
        Py_DECREF(types_module);
        return -1;
    }

    Py_DECREF(types_module);
    Py_INCREF(g_func_type);
    Py_INCREF(g_method_type);
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


void 
print_pyobject(PyObject* obj, const char* name) {
    PyObject* repr = PyObject_Repr(obj);
    if (repr == NULL) {
        printf("Could not get representation of object\n"); fflush(stdout);
        return;
    }
    const char* str = PyUnicode_AsUTF8_abi3(repr);
    if (str == NULL) {
        Py_DECREF(repr);
        printf("Could not convert to UTF-8 string\n"); fflush(stdout);
        return;
    }

    printf("print_pyobject: %s %s\n", name, str); fflush(stdout);

    Py_DECREF(repr);
}