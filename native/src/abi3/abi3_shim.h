#ifndef V8N_ABI3_SHIM_H
#define V8N_ABI3_SHIM_H

// Everything the CPython Stable ABI makes awkward, in one place.
//
// The extension is built with Py_LIMITED_API and ships as a single abi3 wheel
// for CPython 3.12, 3.13 and 3.14.  The same object file therefore has to work
// against three sets of headers and three runtimes, which rules out every
// static PyTypeObject, every non-exported struct, and every header macro whose
// meaning changed between those releases.
//
// Nothing outside this directory may reach for a symbol that Py_LIMITED_API
// hides.  If a new need appears, add the replacement here rather than
// weakening the build.

#include <Python.h>

namespace v8n::abi3 {

// A new reference, taken through the exported function rather than through
// Py_NewRef.  CPython 3.12 made its singletons immortal and reduced
// Py_RETURN_* to a borrowed pointer, which is correct there but wrong when an
// object file compiled against 3.12 headers is loaded by 3.10 or 3.11.  Going
// through Py_IncRef keeps the new-reference contract honest on all of them.
inline PyObject* NewRef(PyObject* value)
{
    Py_IncRef(value);
    return value;
}

// UTF-8 view of a str.  PyUnicode_AsUTF8AndSize is part of the limited API;
// PyUnicode_AsUTF8 is not.
inline const char* Utf8(PyObject* text)
{
    Py_ssize_t size = 0;
    return PyUnicode_AsUTF8AndSize(text, &size);
}

// UTF-8 view plus its length, for callers that need the size.
inline const char* Utf8(PyObject* text, Py_ssize_t* size)
{
    return PyUnicode_AsUTF8AndSize(text, size);
}

// Release an instance allocated by PyType_GenericAlloc.  The free function is
// only reachable through its type slot under the limited API.  Every heap type
// carries one, so the fallback is unreachable rather than load bearing.
inline void FreeInstance(PyTypeObject* type, void* self)
{
    freefunc free_function =
        reinterpret_cast<freefunc>(PyType_GetSlot(type, Py_tp_free));
    if (free_function != nullptr)
        free_function(self);
    else
        PyObject_Del(self);
}

// Close the given standard error if it is not already set, so a conversion
// failure never escapes without a reason attached.
inline void EnsureError(PyObject* exception, const char* message)
{
    if (!PyErr_Occurred())
        PyErr_SetString(exception, message);
}

}  // namespace v8n::abi3

// Trace a member reference.  Py_VISIT is a bare text macro that the limited
// API hides and that cannot live in a namespace, so the traversal guard is
// spelled out once here.
#define VN_VISIT(value)                                                  \
    do                                                                   \
    {                                                                    \
        if ((value) != nullptr)                                          \
        {                                                                \
            int vn_visit_result = visit((PyObject*)(value), arg);        \
            if (vn_visit_result != 0)                                    \
                return vn_visit_result;                                  \
        }                                                                \
    } while (0)

// Bound methods under the limited API.
//
// PyMethod_Check, PyMethod_New and PyMethodObject are all unavailable, yet the
// binding has to recognise a bound method, build one, and follow a method's
// receiver from tp_traverse.  The struct below mirrors CPython's layout for
// the fields that are read; only the receiver is ever touched, and only after
// IsMethod() has confirmed the type.
namespace v8n::abi3
{

using vn_vectorcall_function =
    PyObject* (*)(PyObject*, PyObject* const*, size_t, PyObject*);

struct VnMethodObject
{
    PyObject_HEAD
    PyObject* im_func;
    PyObject* im_self;
    PyObject* im_weakreflist;
    vn_vectorcall_function vectorcall;
};

inline PyObject* MethodSelf(PyObject* method)
{
    return reinterpret_cast<VnMethodObject*>(method)->im_self;
}

// Both predicates answer 1/0 and may set a Python error (-1); callers treat
// any non-zero answer as false and propagate the error.
int IsFunction(PyObject* object);
int IsMethod(PyObject* object);

// Build a bound method without PyMethod_New.
PyObject* NewMethod(PyObject* callable, PyObject* receiver);

// Resolve types.FunctionType and types.MethodType.  Must run during module
// execution, before any predicate is used.
int CacheCallableTypes();

}  // namespace v8n::abi3

#endif
