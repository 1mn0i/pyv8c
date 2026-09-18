#include "isolate_scope.h"

#include "isolate_state.h"

#include <Python.h>

static thread_local IsolateState* active_isolate_state = nullptr;

IsolateState*
current_isolate_state()
{
    return active_isolate_state;
}

IsolateLockScope::IsolateLockScope(IsolateState* state, bool try_only)
    : state_(state),
      previous_(current_isolate_state()),
      isolate_(state->isolate()),
      entered_(false),
      python_owner_(nullptr)
{
    if (try_only && !state_->entry_mutex.try_lock()) return;
    python_owner_ = state_->python_owner;
    Py_XINCREF(python_owner_);
    // A v8::Locker that is contended for blocks until the owning thread leaves
    // the isolate.  Acquiring it while still holding the GIL would starve every
    // other Python thread -- the owning thread itself usually needs the GIL to
    // make progress, so the wait would never end.  Drop the GIL for the
    // duration of the acquisition and re-acquire it before entering the
    // isolate (Python callbacks from inside V8 run while holding it).
    PyThreadState* gil_state = nullptr;
    try
    {
        gil_state = PyEval_SaveThread();
        if (!try_only) state_->entry_mutex.lock();
        locker_.reset(new v8::Locker(isolate_));
    }
    catch (...)
    {
        if (gil_state != nullptr)
        {
            PyEval_RestoreThread(gil_state);
        }
        state_->entry_mutex.unlock();
        Py_XDECREF(python_owner_);
        throw;
    }
    PyEval_RestoreThread(gil_state);
    isolate_->Enter();
    entered_ = true;
    ++state_->entry_depth;
    active_isolate_state = state_;
}

IsolateLockScope::~IsolateLockScope()
{
    if (!entered_) return;
    if (state_->entry_depth == 1) {
        v8::HandleScope handle_scope(isolate_);
        state_->drain_deferred();
    }
    active_isolate_state = previous_;
    if (entered_)
    {
        isolate_->Exit();
        entered_ = false;
    }
    locker_.reset();
    std::vector<PyObject*> waiters;
    if (--state_->entry_depth == 0) waiters.swap(state_->inspector_waiters);
    state_->entry_mutex.unlock();
    if (!waiters.empty()) {
        PyObject *error_type, *error_value, *error_traceback;
        PyErr_Fetch(&error_type, &error_value, &error_traceback);
        for (auto callback : waiters) {
            PyObject* result = PyObject_CallNoArgs(callback);
            if (!result) PyErr_WriteUnraisable(callback);
            else Py_DECREF(result);
            Py_DECREF(callback);
        }
        PyErr_Restore(error_type, error_value, error_traceback);
    }
    Py_XDECREF(python_owner_);
}
