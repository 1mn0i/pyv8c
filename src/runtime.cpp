#include "runtime.h"

#include "isolate_state.h"

#include <new>

RuntimeManager runtime_manager;

RuntimeManager::RuntimeManager() = default;

RuntimeManager::~RuntimeManager()
{
    // Explicit shutdown owns teardown; preserve the previous DLL-unload behavior.
    default_isolate_.release();
    custom_isolates_.clear();
}

bool
RuntimeManager::initialize(PyObject* icu, PyObject* params)
{
    if (is_initialized())
        return false;
    if (!platform_.initialize(icu, params))
        return false;

    std::unique_ptr<IsolateState> state(new (std::nothrow) IsolateState());
    if (!state)
    {
        PyErr_NoMemory();
        platform_.shutdown();
        return false;
    }
    if (!state->initialize())
    {
        platform_.shutdown();
        return false;
    }
    default_isolate_ = std::move(state);
    return true;
}

bool
RuntimeManager::is_initialized() const
{
    return default_isolate_ != nullptr && default_isolate_->is_active();
}

bool
RuntimeManager::require_initialized() const
{
    if (is_initialized())
        return true;
    PyErr_SetString(PyExc_RuntimeError,
                    platform_.is_disposed() ? "V8 has been shut down"
                                            : "V8 has not been initialized");
    return false;
}

IsolateState*
RuntimeManager::create_isolate()
{
    if (!require_initialized())
        return nullptr;

    IsolateState* state = new (std::nothrow) IsolateState();
    if (state == nullptr)
    {
        PyErr_NoMemory();
        return nullptr;
    }
    if (!state->initialize())
    {
        delete state;
        return nullptr;
    }
    try
    {
        custom_isolates_.insert(state);
    }
    catch (const std::bad_alloc&)
    {
        state->shutdown(false);
        delete state;
        PyErr_NoMemory();
        return nullptr;
    }
    return state;
}

bool
RuntimeManager::destroy_isolate(IsolateState* state, bool report_error)
{
    if (state == nullptr || state == default_isolate_.get())
    {
        if (report_error)
            PyErr_SetString(PyExc_RuntimeError, "the default isolate is owned by shutdown_v8()");
        return false;
    }
    auto item = custom_isolates_.find(state);
    if (item == custom_isolates_.end())
        return false;
    if (!state->shutdown(report_error))
        return false;
    custom_isolates_.erase(item);
    delete state;
    return true;
}

IsolateState*
RuntimeManager::find_isolate(v8::Isolate* isolate) const
{
    if (default_isolate_ != nullptr && default_isolate_->isolate() == isolate)
        return default_isolate_.get();
    for (IsolateState* state : custom_isolates_)
    {
        if (state->isolate() == isolate)
            return state;
    }
    return nullptr;
}

bool
RuntimeManager::shutdown(bool report_error)
{
    if (!is_initialized())
        return false;
    if (!custom_isolates_.empty())
    {
        if (report_error)
        {
            PyErr_Format(PyExc_RuntimeError,
                         "cannot shut down V8 while %zu custom isolate(s) are active",
                         custom_isolates_.size());
        }
        return false;
    }
    if (!default_isolate_->shutdown(report_error))
        return false;
    default_isolate_.reset();
    platform_.shutdown();
    return true;
}
