#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "jsundefined.h"


using namespace v8;

PyTypeObject* js_undefined_type = NULL;
PyObject* js_undefined_object = NULL;


static PyType_Slot js_undefined_slots[] = {
    {Py_tp_doc, (void*)""},
    {Py_tp_repr, (void*)PythonBoundary<js_undefined_repr>::call},
    {Py_nb_bool, (void*)PythonBoundary<js_undefined_bool>::call},
    {0, NULL} };

static PyType_Spec js_undefined_spec = {
    "_pyv8.UndefinedType",
    sizeof(js_undefined),
    0,
    Py_TPFLAGS_DEFAULT,
    js_undefined_slots };

int 
js_undefined_type_init()
{
    js_undefined_type = (PyTypeObject*) PyType_FromSpec(&js_undefined_spec);
    if (js_undefined_type == NULL)
    {
        return -1;
    }

    js_undefined_object = PyType_GenericAlloc(js_undefined_type, 0);
    if (js_undefined_object == NULL)
    {
        return -1;
    }
    Py_INCREF((PyObject*)js_undefined_object);
    return 0;
}

PyObject* 
js_undefined_repr(PyObject* self)
{
    static PyObject* undefined_repr = NULL;
    if (undefined_repr == NULL)
    {
        undefined_repr = PyUnicode_InternFromString("undefined");
    }
    Py_INCREF((PyObject*)undefined_repr);
    return undefined_repr;
}


int
js_undefined_bool(PyObject* self)
{
    return 0;
}
