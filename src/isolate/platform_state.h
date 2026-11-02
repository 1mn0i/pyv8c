#ifndef PYV8_PLATFORM_STATE_H
#define PYV8_PLATFORM_STATE_H

#include "bridge_base.h"
#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#endif

#include <libplatform/libplatform.h>
#include <v8.h>

#include <cstddef>
#include <cstdint>
#include <memory>

// The old-space budget the host asked for with --max-old-space-size, in bytes, or
// zero when it named none and V8's own limit is the budget.  V8 reports no such
// number back, and its heap limit covers the other spaces too, so the flag the host
// passed is the only place the intended budget is written down.
std::size_t heap_budget_bytes();
class ForegroundPlatform;
class ForegroundNotifications;

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

    bool pump_foreground_task(v8::Isolate* isolate);
    void notify_isolate_shutdown(v8::Isolate* isolate);
    std::shared_ptr<ForegroundNotifications> foreground_notifications();
    std::uint64_t foreground_token(v8::Isolate* isolate);

private:
    std::unique_ptr<v8::Platform> platform_;
    std::unique_ptr<ForegroundPlatform> foreground_platform_;
    bool initialized_;
    bool disposed_;
};

#endif
