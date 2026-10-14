#include "bridge_base.h"
#include <Python.h>

#include <v8.h>

#include "jsnull.h"


using namespace v8;

PyTypeObject* js_null_type = NULL;
PyObject* js_null_object = NULL;


static PyType_Slot null_slots[] = {
    {Py_tp_doc, (void*)""},
    {Py_tp_repr, (void*)PythonBoundary<js_null_repr>::call},
    {Py_nb_bool, (void*)PythonBoundary<js_null_bool>::call},
    {0, NULL} };

static PyType_Spec null_spec = {
    "pyv8.NullType",
    sizeof(js_null),
    0,
    Py_TPFLAGS_DEFAULT,
    null_slots };

int js_null_type_init()
{
    js_null_type = (PyTypeObject*) PyType_FromSpec(&null_spec);
    if (js_null_type == NULL)
    {
        return -1;
    }

    js_null_object = PyType_GenericAlloc(js_null_type, 0);
    if (js_null_object == NULL)
    {
        return -1;
    }
    Py_INCREF((PyObject*)js_null_object);
    return 0;
}

PyObject* 
js_null_repr(PyObject* self)
{
    static PyObject* null_repr = NULL;
    if (null_repr == NULL)
    {
        null_repr = PyUnicode_InternFromString("null");
    }
    Py_INCREF((PyObject*)null_repr);
    return null_repr;
}


int
js_null_bool(PyObject* self)
{
    return 0;
}
