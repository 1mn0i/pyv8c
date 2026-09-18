#include "execution.h"
#include "isolate_state.h"

ExecutionScope::ExecutionScope(context_c* owner, PyObject* subject) noexcept
    : owner_(owner ? PyV8_NewRef((PyObject*)owner) : nullptr),
      subject_(subject ? PyV8_NewRef(subject) : nullptr) {
    if (!context_is_active(owner)) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is closed");
        return;
    }
    try {
        lock_.emplace(owner->isolate_state);
        if (!lock_->entered()) return;
        if (!context_is_active(owner)) {
            PyErr_SetString(PyExc_RuntimeError, "V8 context is closed");
            return;
        }
        handles_.emplace(lock_->isolate());
        context_ = owner->js_context.Get(lock_->isolate());
        context_scope_.emplace(context_);
        entered_ = true;
    } catch (const std::bad_alloc&) {
        PyErr_NoMemory();
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "failed to enter V8 context");
    }
}
