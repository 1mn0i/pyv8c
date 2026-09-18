#include "kernel/v8_identity.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"

#include <cstring>

namespace v8n
{
namespace
{
// The digest of the icudtl.dat this extension was built against.  Injected by
// native/setup.py from the V8 build the extension links.
#ifdef PYV8_ICU_DATA_SHA256
constexpr const char* kExpectedDigest = PYV8_ICU_DATA_SHA256;
#else
constexpr const char* kExpectedDigest = "";
#endif

constexpr Py_ssize_t kChunkSize = 1024 * 1024;

// hashlib.sha256() over the file, streamed in megabyte chunks.
//
// This runs before ICU exists, so it cannot use a C digest implementation from
// V8 and deliberately goes through the standard library rather than pulling a
// second digest implementation into the wheel.
PyRef
digest_of(PyObject* path)
{
    PyRef hashlib(PyRef::adopt(PyImport_ImportModule("hashlib")));
    if (!hashlib)
        return PyRef();

    PyRef sha256(PyRef::adopt(PyObject_GetAttrString(hashlib.get(), "sha256")));
    if (!sha256)
        return PyRef();

    PyRef hasher(PyRef::adopt(PyObject_CallObject(sha256.get(), nullptr)));
    if (!hasher)
        return PyRef();

    PyRef builtins(PyRef::adopt(PyImport_ImportModule("builtins")));
    if (!builtins)
        return PyRef();

    PyRef open_function(PyRef::adopt(PyObject_GetAttrString(builtins.get(), "open")));
    if (!open_function)
        return PyRef();

    PyRef mode(PyRef::adopt(PyUnicode_FromString("rb")));
    if (!mode)
        return PyRef();

    PyRef open_arguments(PyRef::adopt(PyTuple_Pack(2, path, mode.get())));
    if (!open_arguments)
        return PyRef();

    PyRef stream(PyRef::adopt(
        PyObject_CallObject(open_function.get(), open_arguments.get())));
    if (!stream)
        return PyRef();

    for (;;)
    {
        PyRef chunk(PyRef::adopt(
            PyObject_CallMethod(stream.get(), "read", "n", kChunkSize)));
        if (!chunk)
            return PyRef();

        if (!PyBytes_Check(chunk.get()))
        {
            raise(PyExc_TypeError, "ICU data stream returned a non-bytes value");
            return PyRef();
        }

        const Py_ssize_t size = PyBytes_Size(chunk.get());
        if (size < 0)
            return PyRef();
        if (size == 0)
            break;

        PyRef update(PyRef::adopt(
            PyObject_CallMethod(hasher.get(), "update", "O", chunk.get())));
        if (!update)
            return PyRef();
    }

    return PyRef::adopt(PyObject_CallMethod(hasher.get(), "hexdigest", nullptr));
}
}

const char*
expected_icu_digest()
{
    return kExpectedDigest;
}

bool
verify_icu_data(PyObject* icudtl_path)
{
    if (kExpectedDigest[0] == '\0')
        return true;
    if (icudtl_path == nullptr || !PyUnicode_Check(icudtl_path))
    {
        raise(PyExc_TypeError, "icudtl_path must be a string");
        return false;
    }

    PyRef digest(digest_of(icudtl_path));
    if (!digest)
        return false;

    const char* actual = abi3::Utf8(digest.get());
    if (actual == nullptr)
        return false;

    if (std::strcmp(actual, kExpectedDigest) != 0)
    {
        PyErr_Format(
            PyExc_RuntimeError,
            "icudtl.dat does not match this v8n build (V8 %s): expected SHA-256 %s, got %s; "
            "copy icudtl.dat from the same V8 build as the extension",
            kExpectedDigest,
            actual);
        return false;
    }
    return true;
}

bool
microtask_queue_supported()
{
#ifdef V8_CPPGC_MICROTASK_QUEUE
    return true;
#else
    return false;
#endif
}

}  // namespace v8n
