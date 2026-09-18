#include "v8n/kernel.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/v8_identity.h"

#include <libplatform/libplatform.h>

#include <memory>

namespace v8n
{
namespace
{
std::unique_ptr<v8::Platform> platform_instance;
bool platform_initialized = false;
bool platform_disposed = false;

// Materialise a flags argument and reject anything that is not a sequence of
// strings.
//
// This runs before the already-initialized check so a malformed call is
// rejected identically whether or not V8 is running: a host that passes the
// wrong type should learn that from the call, not from a silent no-op.
bool materialize_flags(PyObject* flags, PyRef* materialised)
{
    if (flags == nullptr || flags == Py_None)
        return true;

    PyRef sequence(PyRef::adopt(
        PySequence_Fast(flags, "flags must be a sequence of strings")));
    if (!sequence)
        return false;

    // PySequence_Fast_GET_SIZE and _GET_ITEM are hidden by the limited API, and
    // a flag list is a handful of entries, so the counting accessors are used
    // rather than optimising this away.
    const Py_ssize_t size = PySequence_Size(sequence.get());
    if (size < 0)
        return false;

    for (Py_ssize_t index = 0; index < size; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(sequence.get(), index)));
        if (!item)
            return false;
        if (!PyUnicode_Check(item.get()))
        {
            raise(PyExc_TypeError, "flags[%zd] must be a string", index);
            return false;
        }
    }

    *materialised = std::move(sequence);
    return true;
}

// Hand every flag to V8.  Flags are a process-global setting, which is why
// they can only be supplied before the platform starts.
bool apply_flags(PyObject* flags)
{
    if (flags == nullptr)
        return true;

    const Py_ssize_t size = PySequence_Size(flags);
    if (size < 0)
        return false;

    for (Py_ssize_t index = 0; index < size; ++index)
    {
        PyRef item(PyRef::adopt(PySequence_GetItem(flags, index)));
        if (!item)
            return false;

        const char* text = abi3::Utf8(item.get());
        if (text == nullptr)
            return false;
        v8::V8::SetFlagsFromString(text);
    }
    return true;
}
}

v8::Platform*
Platform::get()
{
    return platform_instance.get();
}

bool
Platform::is_initialized()
{
    return platform_initialized;
}

bool
Platform::is_disposed()
{
    return platform_disposed;
}

bool
Platform::initialize(PyObject* icudtl_path, PyObject* flags)
{
    if (platform_disposed)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "V8 has been shut down; it cannot be reinitialized in this process");
        return false;
    }

    const bool has_path = icudtl_path != nullptr && icudtl_path != Py_None;
    if (has_path && !PyUnicode_Check(icudtl_path))
    {
        raise(PyExc_TypeError, "icudtl_path must be a string or None");
        return false;
    }

    PyRef flag_list;
    if (!materialize_flags(flags, &flag_list))
        return false;

    if (platform_initialized)
    {
        // Already running: the request is only satisfiable when it asks for
        // nothing new.  Silently ignoring flags would leave a host believing
        // they took effect.
        if (flag_list && PySequence_Size(flag_list.get()) > 0)
        {
            PyErr_SetString(
                PyExc_RuntimeError,
                "V8 is already initialized; flags must be supplied before it starts");
            return false;
        }
        return true;
    }

    // The digest check runs before ICU so a mismatched icudtl.dat produces a
    // readable error instead of a fault inside ICU.
    if (has_path && !verify_icu_data(icudtl_path))
        return false;

    if (!apply_flags(flag_list.get()))
        return false;

    const char* icu_path = nullptr;
    if (has_path)
    {
        icu_path = abi3::Utf8(icudtl_path);
        if (icu_path == nullptr)
            return false;
    }

    if (!v8::V8::InitializeICU(icu_path))
    {
        if (icu_path != nullptr)
            PyErr_Format(PyExc_RuntimeError, "failed to initialize ICU from %s", icu_path);
        else
            PyErr_SetString(PyExc_RuntimeError, "failed to initialize the ICU data linked into V8");
        return false;
    }

    platform_instance = v8::platform::NewDefaultPlatform();
    if (!platform_instance)
    {
        PyErr_SetString(PyExc_RuntimeError, "failed to create the default V8 platform");
        return false;
    }

    v8::V8::InitializePlatform(platform_instance.get());
    if (!v8::V8::Initialize())
    {
        v8::V8::DisposePlatform();
        platform_instance.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize V8");
        return false;
    }

    platform_initialized = true;
    return true;
}

bool
Platform::shutdown()
{
    if (!platform_initialized)
        return true;

    // Give every thread the chance to finish what it abandoned, then refuse if
    // anything is still open.  Disposing V8 under a live isolate is a crash
    // inside V8, so the mistake is reported here instead.
    Isolate::reclaim();

    const std::size_t live = Isolate::live_count();
    if (live != 0)
    {
        PyErr_Format(PyExc_RuntimeError,
                     "cannot shut down V8 while %zu isolate(s) are still open",
                     live);
        return false;
    }

    v8::V8::Dispose();
    v8::V8::DisposePlatform();
    platform_instance.reset();
    platform_initialized = false;
    platform_disposed = true;
    return true;
}

const char*
version()
{
    return v8::V8::GetVersion();
}

}  // namespace v8n
