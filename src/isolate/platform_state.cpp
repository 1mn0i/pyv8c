#include "bridge_base.h"
#include "platform_state.h"

#include "abi3.h"
#include "foreground_platform.h"

#include <cstdlib>
#include <cstring>

using namespace v8;

namespace
{

//: What the host named, in bytes; zero until it names one.
std::size_t g_heap_budget_bytes = 0;

// V8 takes the old-space size in megabytes; the guard compares bytes.
void
record_heap_budget(const char* flag, PyObject* params, Py_ssize_t index)
{
    static const char kFlag[] = "--max-old-space-size";
    const std::size_t length = sizeof(kFlag) - 1;
    if (std::strncmp(flag, kFlag, length) != 0)
        return;
    const char* value = flag + length;
    if (*value == '=')
        ++value;
    else if (*value != '\0')
        return; // a longer flag that merely starts the same way
    else if (index + 1 < PyList_Size(params))
    {
        // The value is the next parameter, the way V8 also accepts two of them.
        value = PyUnicode_AsUTF8_abi3(PyList_GetItem(params, index + 1));
        if (value == nullptr)
        {
            PyErr_Clear();
            return;
        }
    }
    else
        return;

    const long long megabytes = std::strtoll(value, nullptr, 10);
    if (megabytes > 0)
        g_heap_budget_bytes = static_cast<std::size_t>(megabytes) * 1024 * 1024;
}

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
    {
        // A missing or unreadable file is the common case here, and the OSError it
        // raises names no remedy.  Say what to do about it instead.
        if (PyErr_ExceptionMatches(PyExc_OSError))
        {
            const char* icu_path = PyUnicode_AsUTF8_abi3(path);
            PyErr_Clear();
            PyErr_Format(PyExc_RuntimeError,
                         "cannot read ICU data from %s; copy icudtl.dat together with _pyv8,"
                         " or point pyv8.configure(icudtl_path=...) at it",
                         icu_path != nullptr ? icu_path : "<icudtl.dat>");
        }
        return false;
    }

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

std::size_t
heap_budget_bytes()
{
    return g_heap_budget_bytes;
}

PlatformState::PlatformState()
    : initialized_(false), disposed_(false)
{
}

PlatformState::~PlatformState()
{
    // Explicit shutdown_v8() owns deterministic process-level V8 teardown.
    if (initialized_)
    {
        foreground_platform_.release();
        platform_.release();
    }
}

bool PlatformState::pump_foreground_task(v8::Isolate* isolate)
{
    return initialized_ && platform::PumpMessageLoop(
        platform_.get(), isolate, platform::MessageLoopBehavior::kDoNotWait);
}

std::shared_ptr<ForegroundNotifications> PlatformState::foreground_notifications()
{
    return foreground_platform_ ? foreground_platform_->notifications() : nullptr;
}

std::uint64_t PlatformState::foreground_token(v8::Isolate* isolate)
{
    return foreground_platform_ ? foreground_platform_->token(isolate) : 0;
}

void PlatformState::notify_isolate_shutdown(v8::Isolate* isolate)
{
    if (!initialized_ || isolate == nullptr)
        return;
    // NotifyIsolateShutdown in V8 15.4 assumes a runner exists. An isolate
    // which has never requested one still needs a safe shutdown notification.
    platform_->GetForegroundTaskRunner(isolate);
    if (foreground_platform_)
        foreground_platform_->forget(isolate);
    platform::NotifyIsolateShutdown(platform_.get(), isolate);
}

bool
PlatformState::validate_config(PyObject* icu, PyObject* params)
{
    if (icu != nullptr)
    {
        if (!PyUnicode_Check(icu))
        {
            PyErr_SetString(PyExc_TypeError, "icudtl_path parameter must be a string");
            return false;
        }
        if (PyUnicode_AsUTF8_abi3(icu) == nullptr)
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
            if (PyUnicode_AsUTF8_abi3(item) == nullptr)
                return false;
        }
    }
    return true;
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

    if (!validate_config(icu, params))
        return false;

    const char* icudtl_path = nullptr;
    if (icu != nullptr)
    {
        icudtl_path = PyUnicode_AsUTF8_abi3(icu);
        if (icudtl_path == nullptr)
            return false;
    }

    if (params != nullptr)
    {
        const Py_ssize_t size = PyList_Size(params);
        for (Py_ssize_t i = 0; i < size; ++i)
        {
            const char* item_str = PyUnicode_AsUTF8_abi3(PyList_GetItem(params, i));
            if (item_str == nullptr)
                return false;
            record_heap_budget(item_str, params, i);
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
                         "failed to initialize ICU from %s; check that icudtl.dat sits next to"
                         " _pyv8, or point pyv8.configure(icudtl_path=...) at it",
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
    foreground_platform_ = std::make_unique<ForegroundPlatform>(platform_.get());
    V8::InitializePlatform(foreground_platform_.get());
    if (!V8::Initialize())
    {
        V8::DisposePlatform();
        foreground_platform_.reset();
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
    foreground_platform_->notifications()->shutdown();
    foreground_platform_.reset();
    platform_.reset();
    initialized_ = false;
    disposed_ = true;
}
