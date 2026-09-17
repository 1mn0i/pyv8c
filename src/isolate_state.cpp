#include "isolate_state.h"

#include "error_hooks.h"
#include "isolate_scope.h"
#include "pyclass.h"
#include "v8_compat.h"

#include <cassert>
#include <new>

using namespace v8;

struct IsolateState::PythonObjectOwner
{
    explicit PythonObjectOwner(IsolateState* isolate_state)
        : isolate_state(isolate_state), py_object(nullptr), exc_type(nullptr), exc_traceback(nullptr)
    {
    }

    IsolateState* isolate_state;
    Global<Object> object;
    PyObject* py_object;
    PyObject* exc_type;
    PyObject* exc_traceback;
};

void
IsolateState::python_object_weak_callback(
    const WeakCallbackInfo<PythonObjectOwner>& info)
{
    PythonObjectOwner* owner = info.GetParameter();
    owner->object.Reset();
    owner->isolate_state->release_python_owner(owner);
}

IsolateState::IsolateState()
    : isolate_(nullptr),
      live_v8_handles_(0),
      function_template_cache_(nullptr),
      javascript_name_(nullptr),
      uncaught_handler_(nullptr),
      rejection_handler_(nullptr),
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

void
IsolateState::set_error_handlers(PyObject* on_uncaught, PyObject* on_rejection)
{
    Py_XINCREF(on_uncaught);
    Py_XINCREF(on_rejection);
    Py_XDECREF(uncaught_handler_);
    Py_XDECREF(rejection_handler_);
    uncaught_handler_ = on_uncaught;
    rejection_handler_ = on_rejection;
}

void
IsolateState::clear_error_handlers()
{
    Py_CLEAR(uncaught_handler_);
    Py_CLEAR(rejection_handler_);
}

PyObject*
IsolateState::function_template_cache()
{
    if (function_template_cache_ == nullptr)
        function_template_cache_ = PyDict_New();
    return function_template_cache_;
}

void
IsolateState::clear_function_template_cache()
{
    Py_CLEAR(function_template_cache_);
}

PyObject*
IsolateState::javascript_name()
{
    if (javascript_name_ == nullptr)
        javascript_name_ = PyUnicode_InternFromString("javascript");
    return javascript_name_;
}

Local<String>
IsolateState::v8_string(V8StringId id) const
{
    assert(v8_strings_ != nullptr);
    return v8_strings_->get(isolate_, id);
}

bool
IsolateState::track_python_object(Local<Object> object, PyObject* py_object)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "JavaScript object has no Python ownership field");
        return false;
    }

    PythonObjectOwner* owner = new (std::nothrow) PythonObjectOwner(this);
    if (owner == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }

    Py_INCREF(py_object);
    owner->py_object = py_object;
    try
    {
        python_owners_.insert(owner);
    }
    catch (const std::bad_alloc&)
    {
        Py_DECREF(py_object);
        delete owner;
        PyErr_NoMemory();
        return false;
    }
    owner->object.Reset(isolate_, object);
    owner->object.SetWeak(owner, python_object_weak_callback,
                          WeakCallbackType::kParameter);
    object->SetInternalField(PYTHON_OWNER_FIELD, v8_compat::external_new(isolate_, owner));
    return true;
}

bool
IsolateState::attach_exception_state(Local<Object> object,
                                     PyObject* exc_type,
                                     PyObject* exc_traceback)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
        return false;
    Local<Data> owner_value = object->GetInternalField(PYTHON_OWNER_FIELD);
    PythonObjectOwner* owner = static_cast<PythonObjectOwner*>(
        v8_compat::external_value(owner_value));
    if (owner == nullptr || owner->isolate_state != this)
        return false;

    Py_XINCREF(exc_type);
    Py_XINCREF(exc_traceback);
    Py_XDECREF(owner->exc_type);
    Py_XDECREF(owner->exc_traceback);
    owner->exc_type = exc_type;
    owner->exc_traceback = exc_traceback;

    object->SetInternalField(PYTHON_EXCEPTION_TYPE_FIELD,
                             v8_compat::external_new(isolate_, exc_type));
    object->SetInternalField(PYTHON_TRACEBACK_FIELD,
                             v8_compat::external_new(isolate_, exc_traceback));
    return true;
}

void
IsolateState::release_python_owner(PythonObjectOwner* owner)
{
    python_owners_.erase(owner);
    Py_XDECREF(owner->py_object);
    Py_XDECREF(owner->exc_type);
    Py_XDECREF(owner->exc_traceback);
    delete owner;
}

void
IsolateState::clear_python_owners()
{
    while (!python_owners_.empty())
    {
        PythonObjectOwner* owner = *python_owners_.begin();
        owner->object.Reset();
        release_python_owner(owner);
    }
}

bool
IsolateState::shutdown(bool report_error)
{
    if (!is_active())
        return false;

    if (live_v8_handles_ != 0)
    {
        IsolateLockScope scope(this);
        HandleScope handle_scope(isolate_);
        isolate_->RequestGarbageCollectionForTesting(
            Isolate::GarbageCollectionType::kFullGarbageCollection);
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
        clear_function_template_cache();
        Py_CLEAR(javascript_name_);
        clear_python_owners();
        v8_strings_->reset();
    }

    v8_strings_.reset();
    isolate_->Dispose();
    isolate_ = nullptr;
    allocator_.reset();
    disposed_ = true;
    return true;
}
