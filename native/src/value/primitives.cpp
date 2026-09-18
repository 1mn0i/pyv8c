// The two marker objects that stand for JavaScript's null and undefined.
//
// They are singletons, because `pyv8c` compares against them by identity
// (`value is Undefined`), and they are plain Python objects rather than
// retained handles, because null and undefined are primitives: there is nothing
// in V8 for a handle to point at that a conversion could not produce on demand.

#include "value/value_internal.h"

#include "abi3/abi3_shim.h"

namespace v8n::value
{
namespace
{
PyTypeObject* null_type = nullptr;
PyTypeObject* undefined_type = nullptr;
PyObject* null_object = nullptr;
PyObject* undefined_object = nullptr;

PyObject* marker_repr(PyObject* self)
{
    const char* text = Py_TYPE(self) == null_type ? "null" : "undefined";
    return PyUnicode_FromString(text);
}

int marker_bool(PyObject*)
{
    // Neither null nor undefined is truthy, so `bool(Undefined)` is False
    // without a host having to special-case it.
    return 0;
}

PyTypeObject* make_marker_type(const char* name, PyType_Slot* slots)
{
    PyType_Spec spec = {
        name,
        sizeof(PyObject),
        0,
        Py_TPFLAGS_DEFAULT,
        slots,
    };
    return reinterpret_cast<PyTypeObject*>(PyType_FromSpec(&spec));
}
}

PyObject* null_singleton()
{
    return null_object;
}

PyObject* undefined_singleton()
{
    return undefined_object;
}

bool is_null_singleton(PyObject* value)
{
    return value != nullptr && value == null_object;
}

bool is_undefined_singleton(PyObject* value)
{
    return value != nullptr && value == undefined_object;
}

int register_primitives(PyObject* module)
{
    PyType_Slot marker_slots[] = {
        {Py_tp_repr, reinterpret_cast<void*>(marker_repr)},
        {Py_nb_bool, reinterpret_cast<void*>(marker_bool)},
        {Py_tp_doc, const_cast<char*>("A JavaScript primitive marker object.")},
        {0, nullptr},
    };

    null_type = make_marker_type("v8n.NullType", marker_slots);
    undefined_type = make_marker_type("v8n.UndefinedType", marker_slots);
    if (null_type == nullptr || undefined_type == nullptr)
        return -1;

    // The module and this file both keep a reference, so the singletons outlive
    // every realm and a host can hold one across a close.
    null_object = PyType_GenericAlloc(null_type, 0);
    undefined_object = PyType_GenericAlloc(undefined_type, 0);
    if (null_object == nullptr || undefined_object == nullptr)
        return -1;
    Py_INCREF(null_object);
    Py_INCREF(undefined_object);

    if (PyModule_AddObjectRef(module, "Null", null_object) < 0)
        return -1;
    if (PyModule_AddObjectRef(module, "Undefined", undefined_object) < 0)
        return -1;
    return 0;
}

}  // namespace v8n::value
