#ifndef PYV8ENV_ABI3_H
#define PYV8ENV_ABI3_H

#include <Python.h>


#define TP_FREE_ABI3(type, self)                                            \
    freefunc free_func = (freefunc)PyType_GetSlot(type, Py_tp_free);        \
    if (free_func)                                                          \
        free_func(self);                                                    \
    else                                                                    \
        PyObject_Del(self);



typedef PyObject* (*vectorcallfuncabi3)(PyObject* callable, PyObject* const* args, size_t nargsf, PyObject* kwnames);

typedef struct {
    PyObject_HEAD
    PyObject* im_func;  
    PyObject* im_self; 
    PyObject* im_weakreflist;
    vectorcallfuncabi3 vectorcall;
} PyMethodObjectAbi3;

#define PyMethod_GET_SELF_abi3(meth) \
        (((PyMethodObjectAbi3 *)meth) -> im_self)


static inline const char* 
PyUnicode_AsUTF8_abi3(PyObject* unicode) {
    Py_ssize_t size;
    return PyUnicode_AsUTF8AndSize(unicode, &size);
}


static PyObject* g_func_type;
static PyObject* g_method_type;

int cache_FunctionType_init();

int PyFunction_Check_abi3(PyObject* op);
int PyMethod_Check_abi3(PyObject* op);


static inline PyObject *
PyObject_CallMethodNoArgs_abi3(PyObject* self, PyObject* name) {
    return PyObject_CallMethodObjArgs(self, name, NULL);
}

static inline PyObject*
PyObject_CallMethodOneArg_abi3(PyObject* self, PyObject* name, PyObject* arg) {
    return PyObject_CallMethodObjArgs(self, name, arg, NULL);
}

void print_pyobject(PyObject* obj, const char* name);

#endif