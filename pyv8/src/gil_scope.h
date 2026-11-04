#ifndef PYV8_GIL_SCOPE_H
#define PYV8_GIL_SCOPE_H

#include <Python.h>

// GIL bookkeeping.  The Limited API has no PyGILState_Check, so the bridge
// tracks it: an execution window saves the thread state, a callback boundary
// restores it.  A thread that has no Python thread state gets neither scope.

// Saved by the innermost open window; null while the GIL is held.
inline thread_local PyThreadState* bridge_gil_saved_state = nullptr;

// Runs V8 without the GIL, so a thread waiting for the isolate stops holding it.
class GILReleaseScope
{
public:
    GILReleaseScope() noexcept
    {
        if (bridge_gil_saved_state != nullptr)  // an outer window already released it
            return;
        saved_ = PyEval_SaveThread();
        bridge_gil_saved_state = saved_;
        released_ = true;
    }

    ~GILReleaseScope()
    {
        if (!released_)
            return;
        bridge_gil_saved_state = nullptr;
        PyEval_RestoreThread(saved_);
    }

    GILReleaseScope(const GILReleaseScope&) = delete;
    GILReleaseScope& operator=(const GILReleaseScope&) = delete;

    bool released() const noexcept { return released_; }

private:
    PyThreadState* saved_ = nullptr;
    bool released_ = false;
};

// V8 -> Python: take the GIL back.  No-op when this thread already holds it.
class GILAcquireScope
{
public:
    GILAcquireScope() noexcept
    {
        PyThreadState* saved = bridge_gil_saved_state;
        if (saved == nullptr)
            return;
        bridge_gil_saved_state = nullptr;
        PyEval_RestoreThread(saved);
        restored_ = true;
    }

    ~GILAcquireScope()
    {
        if (!restored_)
            return;
        bridge_gil_saved_state = PyEval_SaveThread();
    }

    GILAcquireScope(const GILAcquireScope&) = delete;
    GILAcquireScope& operator=(const GILAcquireScope&) = delete;

private:
    bool restored_ = false;
};

// One V8 call with the GIL released.  The operation must touch V8 only.
template <class Operation>
auto without_gil(Operation&& operation) -> decltype(operation())
{
    GILReleaseScope window;
    return operation();
}

#endif
