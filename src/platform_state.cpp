#include "platform_state.h"

#include "abi3.h"

using namespace v8;

PlatformState::PlatformState()
    : initialized_(false), disposed_(false)
{
}

PlatformState::~PlatformState()
{
    // Explicit shutdown_v8() owns deterministic process-level V8 teardown.
    if (initialized_)
        platform_.release();
}

bool
PlatformState::initialize(PyObject* icu, PyObject* params)
{
    if (initialized_)
        return true;
    if (disposed_)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "V8 cannot be reinitialized after shutdown_v8()");
        return false;
    }

    const char* icudtl_path = nullptr;
    if (icu != nullptr)
    {
        if (!PyUnicode_Check(icu))
        {
            PyErr_SetString(PyExc_TypeError, "icudtl_path parameter must be a string");
            return false;
        }
        icudtl_path = PyUnicode_AsUTF8_abi3(icu);
        if (icudtl_path == nullptr)
            return false;
    }

    if (params != nullptr)
    {
        if (!PyList_Check(params))
        {
            PyErr_SetString(PyExc_TypeError, "params parameter must be a list");
            return false;
        }
        const Py_ssize_t size = PyList_Size(params);
        for (Py_ssize_t i = 0; i < size; ++i)
        {
            PyObject* item = PyList_GetItem(params, i);
            if (!PyUnicode_Check(item))
            {
                PyErr_SetString(PyExc_TypeError, "params list must only contain strings");
                return false;
            }
            const char* item_str = PyUnicode_AsUTF8_abi3(item);
            if (item_str == nullptr)
                return false;
            V8::SetFlagsFromString(item_str);
        }
    }

    if (icudtl_path != nullptr)
        V8::InitializeICU(icudtl_path);
    else
        V8::InitializeICU();

    platform_ = platform::NewDefaultPlatform();
    if (!platform_)
    {
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 platform");
        return false;
    }
    V8::InitializePlatform(platform_.get());
    if (!V8::Initialize())
    {
        V8::DisposePlatform();
        platform_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize V8");
        return false;
    }

    initialized_ = true;
    return true;
}

void
PlatformState::shutdown()
{
    if (!initialized_)
        return;
    V8::Dispose();
    V8::DisposePlatform();
    platform_.reset();
    initialized_ = false;
    disposed_ = true;
}
