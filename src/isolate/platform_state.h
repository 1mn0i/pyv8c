#ifndef PYV8_PLATFORM_STATE_H
#define PYV8_PLATFORM_STATE_H

#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#endif

#include <libplatform/libplatform.h>
#include <v8.h>

#include <memory>

class PlatformState
{
public:
    PlatformState();
    ~PlatformState();

    // Checks what a caller passed without touching V8, so a bad path or flag list is
    // reported where it was given -- at configure() time as well as at startup.
    static bool validate_config(PyObject* icu, PyObject* params);

    bool initialize(PyObject* icu, PyObject* params);
    void shutdown();

    bool is_initialized() const { return initialized_; }
    bool is_disposed() const { return disposed_; }

private:
    std::unique_ptr<v8::Platform> platform_;
    bool initialized_;
    bool disposed_;
};

#endif
