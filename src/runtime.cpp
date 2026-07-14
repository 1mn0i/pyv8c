#include "pyv8.h"

#include "pyclass.h"

#include <cassert>
#include <new>

using namespace v8;

struct PythonObjectOwner
{
    explicit PythonObjectOwner(RuntimeState* runtime)
        : runtime(runtime), py_object(nullptr), exc_type(nullptr), exc_traceback(nullptr)
    {
    }

    RuntimeState* runtime;
    Global<Object> object;
    PyObject* py_object;
    PyObject* exc_type;
    PyObject* exc_traceback;
};

static void
python_object_weak_callback(const WeakCallbackInfo<PythonObjectOwner>& info)
{
    PythonObjectOwner* owner = info.GetParameter();
    owner->object.Reset();
    owner->runtime->release_python_owner(owner);
}

RuntimeState runtime_state;
Isolate*& isolate = runtime_state.isolate;

RuntimeState::RuntimeState()
    : isolate(nullptr),
      live_v8_handles_(0),
      function_template_cache_(nullptr),
      javascript_name_(nullptr),
      disposed_(false)
{
}

RuntimeState::~RuntimeState()
{
    // V8 cannot be torn down safely during arbitrary DLL unloading. Explicit
    // shutdown_v8() owns the deterministic cleanup path.
    if (isolate != nullptr)
    {
        v8_strings_.release();
        platform_.release();
        allocator_.release();
    }
}

bool
RuntimeState::initialize(PyObject* icu, PyObject* params)
{
    if (is_initialized())
    {
        return false;
    }
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
        {
            return false;
        }
    }

    if (params != nullptr)
    {
        if (!PyList_Check(params))
        {
            PyErr_SetString(PyExc_TypeError, "params parameter must be a list");
            return false;
        }
        Py_ssize_t size = PyList_Size(params);
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
            {
                return false;
            }
            V8::SetFlagsFromString(item_str);
        }
    }

    if (icudtl_path != nullptr)
    {
        V8::InitializeICU(icudtl_path);
    }
    else
    {
        V8::InitializeICU();
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

    allocator_.reset(ArrayBuffer::Allocator::NewDefaultAllocator());
    if (!allocator_)
    {
        V8::Dispose();
        V8::DisposePlatform();
        platform_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 ArrayBuffer allocator");
        return false;
    }

    Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = allocator_.get();
    isolate = Isolate::New(create_params);
    if (isolate == nullptr)
    {
        allocator_.reset();
        V8::Dispose();
        V8::DisposePlatform();
        platform_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 isolate");
        return false;
    }

    isolate->SetCaptureStackTraceForUncaughtExceptions(
        true, 100,
        static_cast<StackTrace::StackTraceOptions>(
            StackTrace::kOverview | StackTrace::kScriptId));

    try
    {
        v8_strings_ = std::make_unique<V8StringCache>();
    }
    catch (const std::bad_alloc&)
    {
        isolate->Dispose();
        isolate = nullptr;
        V8::Dispose();
        V8::DisposePlatform();
        allocator_.reset();
        platform_.reset();
        PyErr_NoMemory();
        return false;
    }

    bool strings_initialized;
    {
        Locker locker(isolate);
        Isolate::Scope isolate_scope(isolate);
        HandleScope handle_scope(isolate);
        strings_initialized = v8_strings_->initialize(isolate);
    }
    if (!strings_initialized)
    {
        v8_strings_.reset();
        isolate->Dispose();
        isolate = nullptr;
        V8::Dispose();
        V8::DisposePlatform();
        allocator_.reset();
        platform_.reset();
        PyErr_SetString(PyExc_RuntimeError, "failed to initialize V8 string cache");
        return false;
    }
    return true;
}

bool
RuntimeState::require_initialized() const
{
    if (isolate != nullptr)
    {
        return true;
    }
    PyErr_SetString(PyExc_RuntimeError,
                    disposed_ ? "V8 has been shut down"
                              : "V8 has not been initialized");
    return false;
}

void
RuntimeState::retain_v8_handle()
{
    ++live_v8_handles_;
}

void
RuntimeState::release_v8_handle()
{
    if (live_v8_handles_ > 0)
    {
        --live_v8_handles_;
    }
}

PyObject*
RuntimeState::function_template_cache()
{
    if (function_template_cache_ == nullptr)
    {
        function_template_cache_ = PyDict_New();
    }
    return function_template_cache_;
}

void
RuntimeState::clear_function_template_cache()
{
    Py_CLEAR(function_template_cache_);
}

PyObject*
RuntimeState::javascript_name()
{
    if (javascript_name_ == nullptr)
    {
        javascript_name_ = PyUnicode_InternFromString("javascript");
    }
    return javascript_name_;
}

Local<String>
RuntimeState::v8_string(V8StringId id) const
{
    assert(v8_strings_ != nullptr);
    return v8_strings_->get(isolate, id);
}

bool
RuntimeState::track_python_object(Local<Object> object, PyObject* py_object)
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
    owner->object.Reset(isolate, object);
    owner->object.SetWeak(owner, python_object_weak_callback,
                          WeakCallbackType::kParameter);
    object->SetInternalField(PYTHON_OWNER_FIELD, External::New(isolate, owner));
    return true;
}

bool
RuntimeState::attach_exception_state(Local<Object> object,
                                     PyObject* exc_type,
                                     PyObject* exc_traceback)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
    {
        return false;
    }
    Local<Data> owner_value = object->GetInternalField(PYTHON_OWNER_FIELD);
    PythonObjectOwner* owner = static_cast<PythonObjectOwner*>(
        owner_value.As<External>()->Value());
    if (owner == nullptr)
    {
        return false;
    }
    Py_XINCREF(exc_type);
    Py_XINCREF(exc_traceback);
    Py_XDECREF(owner->exc_type);
    Py_XDECREF(owner->exc_traceback);
    owner->exc_type = exc_type;
    owner->exc_traceback = exc_traceback;

    object->SetInternalField(PYTHON_EXCEPTION_TYPE_FIELD,
                             External::New(isolate, exc_type));
    object->SetInternalField(PYTHON_TRACEBACK_FIELD,
                             External::New(isolate, exc_traceback));
    return true;
}

void
RuntimeState::release_python_owner(PythonObjectOwner* owner)
{
    python_owners_.erase(owner);
    Py_XDECREF(owner->py_object);
    Py_XDECREF(owner->exc_type);
    Py_XDECREF(owner->exc_traceback);
    delete owner;
}

void
RuntimeState::clear_python_owners()
{
    while (!python_owners_.empty())
    {
        PythonObjectOwner* owner = *python_owners_.begin();
        owner->object.Reset();
        release_python_owner(owner);
    }
}

bool
RuntimeState::shutdown(bool report_error)
{
    if (!is_initialized())
    {
        return false;
    }
    if (live_v8_handles_ != 0)
    {
        Locker locker(isolate);
        Isolate::Scope isolate_scope(isolate);
        HandleScope handle_scope(isolate);
        isolate->RequestGarbageCollectionForTesting(
            Isolate::GarbageCollectionType::kFullGarbageCollection);
    }
    if (live_v8_handles_ != 0)
    {
        if (report_error)
        {
            PyErr_Format(PyExc_RuntimeError,
                         "cannot shut down V8 while %zu persistent handle(s) are active",
                         live_v8_handles_);
        }
        return false;
    }

    {
        Locker locker(isolate);
        Isolate::Scope isolate_scope(isolate);
        HandleScope handle_scope(isolate);
        clear_function_template_cache();
        Py_CLEAR(javascript_name_);
        clear_python_owners();
        v8_strings_->reset();
    }

    v8_strings_.reset();
    isolate->Dispose();
    isolate = nullptr;
    V8::Dispose();
    V8::DisposePlatform();
    allocator_.reset();
    platform_.reset();
    disposed_ = true;
    return true;
}
