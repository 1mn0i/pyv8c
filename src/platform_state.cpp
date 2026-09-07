#include "platform_state.h"

#include "abi3.h"

#include <cstring>

using namespace v8;

namespace
{

bool
verify_icu_data_fingerprint(PyObject* path)
{
#ifndef PYV8_ICU_DATA_SHA256
    return true;
#else
    PyOwnedRef hashlib(PyImport_ImportModule("hashlib"));
    if (hashlib.get() == nullptr)
        return false;
    PyOwnedRef sha256(PyObject_GetAttrString(hashlib.get(), "sha256"));
    if (sha256.get() == nullptr)
        return false;
    PyOwnedRef hasher(PyObject_CallObject(sha256.get(), nullptr));
    if (hasher.get() == nullptr)
        return false;

    PyOwnedRef builtins(PyImport_ImportModule("builtins"));
    if (builtins.get() == nullptr)
        return false;
    PyOwnedRef open_function(PyObject_GetAttrString(builtins.get(), "open"));
    if (open_function.get() == nullptr)
        return false;
    PyOwnedRef mode(PyUnicode_FromString("rb"));
    if (mode.get() == nullptr)
        return false;
    PyOwnedRef open_args(PyTuple_Pack(2, path, mode.get()));
    if (open_args.get() == nullptr)
        return false;
    PyOwnedRef stream(PyObject_CallObject(open_function.get(), open_args.get()));
    if (stream.get() == nullptr)
        return false;

    while (true)
    {
        PyOwnedRef chunk(PyObject_CallMethod(stream.get(), "read", "i", 1024 * 1024));
        if (chunk.get() == nullptr)
            return false;
        if (!PyBytes_Check(chunk.get()))
        {
            PyErr_SetString(PyExc_TypeError, "ICU data stream returned a non-bytes value");
            return false;
        }
        const Py_ssize_t chunk_size = PyBytes_Size(chunk.get());
        if (chunk_size < 0)
            return false;
        if (chunk_size == 0)
            break;
        PyOwnedRef update_result(
            PyObject_CallMethod(hasher.get(), "update", "O", chunk.get()));
        if (update_result.get() == nullptr)
            return false;
    }

    PyOwnedRef digest(PyObject_CallMethod(hasher.get(), "hexdigest", nullptr));
    if (digest.get() == nullptr)
        return false;
    const char* actual_sha256 = PyUnicode_AsUTF8_abi3(digest.get());
    if (actual_sha256 == nullptr)
        return false;
    if (std::strcmp(actual_sha256, PYV8_ICU_DATA_SHA256) != 0)
    {
        PyErr_Format(
            PyExc_RuntimeError,
            "ICU data does not match this _pyv8 build (V8 %s): expected SHA-256 %s, got %s; copy icudtl.dat together with _pyv8",
            V8::GetVersion(), PYV8_ICU_DATA_SHA256, actual_sha256);
        return false;
    }
    return true;
#endif
}

}

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

    if (icu != nullptr && !verify_icu_data_fingerprint(icu))
        return false;

    const bool icu_initialized = icudtl_path != nullptr
                                     ? V8::InitializeICU(icudtl_path)
                                     : V8::InitializeICU();
    if (!icu_initialized)
    {
        if (icudtl_path != nullptr)
            PyErr_Format(PyExc_RuntimeError,
                         "failed to initialize ICU from %s",
                         icudtl_path);
        else
            PyErr_SetString(PyExc_RuntimeError, "failed to initialize bundled ICU data");
        return false;
    }

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
