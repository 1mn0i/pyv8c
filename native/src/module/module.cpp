#include <Python.h>

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/v8_identity.h"
#include "module/registry.h"
#include "v8n/boundary.h"
#include "v8n/kernel.h"

namespace
{

PyObject*
module_v8_version(PyObject*, PyObject*)
{
    return PyUnicode_FromString(v8n::version());
}

PyObject*
module_initialize(PyObject*, PyObject* args, PyObject* kwargs)
{
    PyObject* icudtl_path = Py_None;
    PyObject* flags = Py_None;
    static const char* keywords[] = {"icudtl_path", "flags", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO",
                                     const_cast<char**>(keywords),
                                     &icudtl_path, &flags))
        return nullptr;

    if (!v8n::Platform::initialize(icudtl_path, flags))
        return nullptr;
    return v8n::abi3::NewRef(Py_None);
}

PyObject*
module_shutdown(PyObject*, PyObject*)
{
    if (!v8n::Platform::shutdown())
        return nullptr;
    return v8n::abi3::NewRef(Py_None);
}

PyObject*
module_is_initialized(PyObject*, PyObject*)
{
    return PyBool_FromLong(v8n::Platform::is_initialized() ? 1 : 0);
}

PyObject*
module_icu_digest(PyObject*, PyObject*)
{
    return PyUnicode_FromString(v8n::expected_icu_digest());
}

PyObject*
module_microtask_queues_supported(PyObject*, PyObject*)
{
    return PyBool_FromLong(v8n::microtask_queue_supported() ? 1 : 0);
}

PyObject*
module_set_error_map(PyObject*, PyObject* mapping)
{
    if (!v8n::set_error_map(mapping))
        return nullptr;
    return v8n::abi3::NewRef(Py_None);
}

PyMethodDef module_methods[] = {
    {"v8_version", module_v8_version, METH_NOARGS,
     "Return the V8 version this extension was built against."},
    {"initialize", reinterpret_cast<PyCFunction>(module_initialize),
     METH_VARARGS | METH_KEYWORDS,
     "initialize(icudtl_path=None, flags=()) -> None\n\n"
     "Start the process-wide V8 platform.  Idempotent."},
    {"shutdown", module_shutdown, METH_NOARGS,
     "shutdown() -> None\n\n"
     "Dispose the process-wide V8 platform.  No isolate may exist."},
    {"is_initialized", module_is_initialized, METH_NOARGS,
     "Return whether the process-wide V8 platform is running."},
    {"icu_digest", module_icu_digest, METH_NOARGS,
     "Return the ICU data digest this extension requires, or an empty string."},
    {"microtask_queues_supported", module_microtask_queues_supported, METH_NOARGS,
     "Return whether this build can use v8::MicrotaskQueue."},
    {"set_error_map", module_set_error_map, METH_O,
     "set_error_map(mapping) -> None\n\n"
     "Register the JavaScript error name -> Python exception mapping.  Values "
     "may be exception classes, or callables taking (name, message)."},
    {nullptr, nullptr, 0, nullptr},
};

int
module_exec(PyObject* module)
{
    // Order matters: the callable cache is needed by every conversion, and the
    // interpreter check has to come first so a forbidden interpreter never
    // touches process-global state.
    if (!v8n::module::require_main_interpreter())
        return -1;
    if (v8n::abi3::CacheCallableTypes() < 0)
        return -1;
    if (v8n::module::register_types(module) < 0)
        return -1;
    return 0;
}

PyModuleDef_Slot module_slots[] = {
    {Py_mod_exec, reinterpret_cast<void*>(module_exec)},
    {0, nullptr},
};

PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_v8n",
    "Native V8 bindings for pyv8c.",
    0,
    module_methods,
    module_slots,
    nullptr,
    nullptr,
    nullptr,
};

}  // namespace

PyMODINIT_FUNC
PyInit__v8n()
{
    // Multi-phase init runs module_exec in every interpreter, which is what
    // lets require_main_interpreter() reject the others instead of silently
    // handing them the same process-global V8.
    return PyModuleDef_Init(&module_definition);
}
