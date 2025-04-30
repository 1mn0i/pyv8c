#ifndef POLYFILL_H
#define POLYFILL_H

#include <Python.h>

// on both, demand trailing semicolon
#define PyErr_PROPAGATE_RET(x, retval) \
    if (x == NULL)                     \
    {                                  \
        return retval;                 \
    }                                  \
    else                               \
        (void)NULL

#define PyErr_PROPAGATE(x) PyErr_PROPAGATE_RET(x, NULL)
#define PyErr_PROPAGATE_(x) PyErr_PROPAGATE_RET(x, -1)

inline extern int 
PyString_StartsWithString(PyObject *str, const char *prefix)
{
    PyObject *result = PyObject_CallMethod(str, (char *)"startswith", (char *)"s", prefix);
    int retval = (result == Py_True);
    Py_XDECREF(result);
    return retval;
}

#endif

