#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <libplatform/libplatform.h>
#include <cstring>

#include "context.h"
#include "convert.h"
#include "script.h"
#include "pyclass.h"
#include "jsobject.h"
#include "jsvalue.h"
#include "jsfunction.h"
#include "jspromise.h"
#include "jstypedarray.h"
#include "pyfunction.h"
#include "debugger.h"

#ifdef V8_OS_WIN
#define LIB_EXPORT __declspec(dllexport)
#else // V8_OS_WIN
#define LIB_EXPORT __attribute__((visibility("default")))
#endif

using namespace v8;

std::unique_ptr<Platform> current_platform = NULL;
Isolate *isolate = NULL;

static PyObject *
py_initialize_v8(PyObject *self, PyObject *args, PyObject *kw)
{

    if (current_platform != NULL)
    {
        Py_RETURN_FALSE;
    }
    static char *keywords[] = {"icudtl_path", "params", NULL};
    PyObject *icu = NULL;
    PyObject *params = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kw, "|OO", keywords, &icu, &params))
    {
        return NULL;
    }
    if (icu != NULL)
    {
        if (!PyUnicode_Check(icu))
        {
            PyErr_SetString(PyExc_TypeError, "icudtl_path parameter must be a string");
            return NULL;
        }
        const char *icudtl_path = PyUnicode_AsUTF8_abi3(icu);
        if (icudtl_path == NULL)
            return NULL;
        V8::InitializeICU(icudtl_path);
    }
    else
    {
        V8::InitializeICU();
    }

    if (params != NULL)
    {
        if (!PyList_Check(params))
        {
            PyErr_SetString(PyExc_TypeError, "params parameter must be a list");
            return NULL;
        }

        Py_ssize_t size = PyList_Size(params);
        for (Py_ssize_t i = 0; i < size; ++i)
        {
            PyObject *item = PyList_GetItem(params, i);
            if (!PyUnicode_Check(item))
            {
                PyErr_SetString(PyExc_TypeError, "params list must only contain strings");
                return NULL;
            }
            const char *item_str = PyUnicode_AsUTF8_abi3(item);
            if (item_str == NULL)
                return NULL;
            V8::SetFlagsFromString(item_str);
        }
    }
    current_platform = platform::NewDefaultPlatform();
    V8::InitializePlatform(current_platform.get());
    V8::Initialize();

    Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = ArrayBuffer::Allocator::NewDefaultAllocator();
    isolate = Isolate::New(create_params);
    isolate->SetCaptureStackTraceForUncaughtExceptions(true, 100,
                                                       // sadly the v8 people screwed up and require me to cast this into to an enum
                                                       static_cast<StackTrace::StackTraceOptions>(StackTrace::kOverview | StackTrace::kScriptId));

    create_memes_plz_thx();

    Py_RETURN_TRUE;
}

PyObject *
mark_hidden(PyObject *self, PyObject *thing)
{
    if (PyObject_SetAttrString(thing, "__v8_hidden__", Py_True) < 0)
    {
        return NULL;
    }
    Py_INCREF(thing);
    return thing;
}

PyObject *
mark_unconstructable(PyObject *self, PyObject *thing)
{
    if (PyObject_SetAttrString(thing, "__v8_unconstructable__", Py_True) < 0)
    {
        return NULL;
    }
    Py_INCREF(thing);
    return thing;
}

PyObject *
trigger_low_memory_notification(PyObject *self, PyObject *args)
{
    IN_V8;
    isolate->LowMemoryNotification();
    Py_RETURN_NONE;
}

PyObject *
construct_new_object(PyObject *self, PyObject *args)
{

    int argc = (int)PyTuple_Size(args);
    if (argc == 0)
    {
        PyErr_SetString(PyExc_TypeError, "First argument must be a constructor function.");
        return NULL;
    }

    PyObject *constructor = PyTuple_GetItem(args, 0);
    if (!PyObject_TypeCheck(constructor, js_function_type))
    {
        PyErr_SetString(PyExc_TypeError, "First argument must be a constructor function.");
        return NULL;
    }

    js_function *function = (js_function *)constructor;

    IN_V8;
    Local<Object> object = function->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked())
    JS_TRY

    if (!object->IsConstructor())
    {
        PyErr_SetString(PyExc_TypeError, "First argument must be a constructor function.");
        return NULL;
    }

    // exclude first argument
    argc--;

    if (!context_setup_timeout(context))
        return NULL;
    MaybeLocal<Value> result;
#ifndef _WIN32
    // error C2131: expression did not evaluate to a constant on Windows
    if (argc <= 16)
    {
        Local<Value> argv[argc];
        for (long i = 0; i < argc; i++)
        {
            argv[i] = js_from_py(PyTuple_GetItem(args, i + 1), context);
        }
        result = object->CallAsConstructor(isolate->GetCurrentContext(), argc, argv);
    }
    else
    {
#endif
        Local<Value> *argv = new Local<Value>[argc];
        for (long i = 0; i < argc; i++)
        {
            argv[i] = js_from_py(PyTuple_GetItem(args, i + 1), context);
        }
        result = object->CallAsConstructor(isolate->GetCurrentContext(), argc, argv);
        delete[] argv;
#ifndef _WIN32
    }
#endif

    if (!context_cleanup_timeout(context))
        return NULL;

    PY_PROPAGATE_JS;

    return py_from_js(result.ToLocalChecked(), context);
}

PyObject *
context_get_current(PyObject *self, PyObject *args)
{
    Local<Context> current_context = isolate->GetCurrentContext();
    if (current_context.IsEmpty())
    {
        Py_RETURN_NONE;
    }
    PyObject *context = (PyObject *)current_context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    Py_INCREF(context);
    return context;
}

PyObject *
v8_version(PyObject *self, PyObject *args)
{
    const char *version = v8::V8::GetVersion();
    return PyUnicode_FromString(version);
}

PyObject *
v8_gc(PyObject *self, PyObject *args)
{
    IN_V8;
    isolate->RequestGarbageCollectionForTesting(Isolate::GarbageCollectionType::kFullGarbageCollection);
    Py_RETURN_NONE;
}

static PyMethodDef v8_methods[] = {
    {"hidden", mark_hidden, METH_O, ""},
    {"unconstructable", mark_unconstructable, METH_O, ""},
    {"current_context", context_get_current, METH_NOARGS, "get current v8 context"},
    {"new", construct_new_object, METH_VARARGS, "Creates a new JavaScript object from a given constructor function"},
    {"low_memory_notification", trigger_low_memory_notification, METH_NOARGS, "Trigger V8 low memory notification"},
    {"init_v8", (PyCFunction)py_initialize_v8, METH_VARARGS | METH_KEYWORDS, "initialize v8 platform and isolate"},
    {"v8_version", v8_version, METH_NOARGS, "v8 version"},
    {"v8_gc", v8_gc, METH_NOARGS, "v8 gc"},
    {NULL},
};

#define FAIL NULL

PyMODINIT_FUNC PyInit__pyv8()
{
    static struct PyModuleDef v8_module_def = {PyModuleDef_HEAD_INIT};
    v8_module_def.m_name = "_pyv8";
    v8_module_def.m_size = -1;
    v8_module_def.m_methods = v8_methods;
    PyObject *module = PyModule_Create(&v8_module_def);
    if (module == NULL)
        return FAIL;

    if (cache_FunctionType_init() < 0)
        return FAIL;

    if (context_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "Context", (PyObject *)context_type) < 0)
        return FAIL;

    if (script_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "Script", (PyObject *)script_type) < 0)
        return FAIL;

    if (debugger_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "Debugger", (PyObject *)debugger_type) < 0)
        return FAIL;

    if (py_function_type_init() < 0)
        return FAIL;

    if (py_class_type_init() < 0)
        return FAIL;

    if (js_object_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JSObject", (PyObject *)js_object_type) < 0)
        return FAIL;

    if (js_value_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JSValue", (PyObject*)js_value_type) < 0)
        return FAIL;

    if (js_promise_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JSPromise", (PyObject *)js_promise_type) < 0)
        return FAIL;

    if (js_typedarray_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JSTypeArray", (PyObject *)js_typedarray_type) < 0)
        return FAIL;

    if (js_function_type_init() < 0)
        return FAIL;

    if (PyModule_AddObject(module, "JSFunction", (PyObject *)js_function_type) < 0)
        return FAIL;

    if (js_exception_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JSException", (PyObject *)js_exception_type) < 0)
        return FAIL;

    if (js_terminated_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "JavaScriptTerminated", (PyObject *)js_terminated_type) < 0)
        return FAIL;

    if (js_null_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "Null", js_null_object) < 0)
        return FAIL;

    if (js_undefined_type_init() < 0)
        return FAIL;
    if (PyModule_AddObject(module, "Undefined", js_undefined_object) < 0)
        return FAIL;

    return module;
}
