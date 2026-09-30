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

#endif
