#include "isolate_state.h"

#include "debugger.h"
#include "error_hooks.h"
#include "isolate_scope.h"
#include "context.h"
#include "v8_compat.h"

#include <cassert>
#include <new>

using namespace v8;

IsolateState::IsolateState()
    : isolate_(nullptr),
      live_v8_handles_(0),
      error_hooks_installed_(false),
      disposed_(false)
{
}

IsolateState::~IsolateState()
{
    // Avoid V8 teardown during arbitrary extension-module unloading.
    if (isolate_ != nullptr)
    {
        v8_strings_.release();
        allocator_.release();
    }
}

bool
IsolateState::initialize()
{
    if (is_active())
        return true;
    if (disposed_)
    {
        PyErr_SetString(PyExc_RuntimeError, "V8 isolate has been closed");
        return false;
    }

    allocator_.reset(ArrayBuffer::Allocator::NewDefaultAllocator());
    if (!allocator_)
    {
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 ArrayBuffer allocator");
        return false;
    }

    Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = allocator_.get();
    isolate_ = Isolate::New(create_params);
    if (isolate_ == nullptr)
    {
        allocator_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 isolate");
        return false;
    }

    isolate_->SetData(0, this);
    watchdog_ = std::make_unique<Watchdog>(isolate_);
    isolate_->SetCaptureStackTraceForUncaughtExceptions(
        true, 100,
        static_cast<StackTrace::StackTraceOptions>(
            StackTrace::kOverview | StackTrace::kScriptId));

    try
    {
        v8_strings_ = std::make_unique<V8StringCache>();
    }
    catch (const std::bad_alloc&)
    {
        isolate_->Dispose();
        isolate_ = nullptr;
        allocator_.reset();
        PyErr_NoMemory();
        return false;
    }

    bool strings_initialized;
    {
        IsolateLockScope scope(this);
        HandleScope handle_scope(isolate_);
        strings_initialized = v8_strings_->initialize(isolate_);
    }
    if (!strings_initialized)
    {
        v8_strings_.reset();
        isolate_->Dispose();
        isolate_ = nullptr;
        allocator_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize V8 string cache");
        return false;
    }
    return true;
}

bool
IsolateState::require_active() const
{
    if (isolate_ != nullptr)
        return true;
    PyErr_SetString(PyExc_RuntimeError,
                    disposed_ ? "V8 isolate has been closed"
                              : "V8 isolate has not been initialized");
    return false;
}

void
IsolateState::retain_v8_handle()
{
    ++live_v8_handles_;
}

void
IsolateState::release_v8_handle()
{
    if (live_v8_handles_ > 0)
        --live_v8_handles_;
}

bool
IsolateState::shutdown(bool report_error)
{
    if (!is_active())
        return false;

    if (entry_users != 0 || entry_depth != 0) {
        if (report_error) PyErr_SetString(PyExc_RuntimeError, "cannot close V8 isolate during active execution");
        return false;
    }

    if (live_v8_handles_ != 0)
    {
        IsolateLockScope scope(this);
        HandleScope handle_scope(isolate_);
        isolate_->LowMemoryNotification();
        drain_deferred();
    }
    if (live_v8_handles_ != 0)
    {
        if (report_error)
        {
            PyErr_Format(PyExc_RuntimeError,
                         "cannot close V8 isolate while %zu persistent handle(s) are active",
                         live_v8_handles_);
        }
        return false;
    }

    {
        IsolateLockScope scope(this);
        HandleScope handle_scope(isolate_);
        // The reporters go first: they hold references into Python and could
        // otherwise be called while the isolate is already being torn down.
        error_hooks_uninstall(this);
        // Inspectors go with the isolate that owns them, and this is the last
        // point at which that is still true.
        debugger_isolate_closed(isolate_);
        clear_python_owners();
        drain_deferred();
        for (auto* data : callback_data_) {
            data->external.Reset();
            delete data;
        }
        callback_data_.clear();
        v8_strings_->reset();
    }

    // The isolate is about to go away, and a watchdog that reached it afterwards
    // would be a call into freed memory.  A shutdown that failed above left it
    // running, which is what keeps a still-open isolate on its deadline.
    if (watchdog_ != nullptr)
        watchdog_->shutdown();

    v8_strings_.reset();
    isolate_->Dispose();
    isolate_ = nullptr;
    allocator_.reset();
    disposed_ = true;
    return true;
}
