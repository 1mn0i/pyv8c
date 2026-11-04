#ifndef PYV8_EXECUTION_H
#define PYV8_EXECUTION_H

#include "bridge_base.h"
#include <v8.h>
#include <Python.h>
#include <optional>
#include "abi3.h"
#include "context.h"
#include "isolate_scope.h"

// Destruction order keeps Python owners alive until V8 scopes and locks exit.
class ExecutionScope {
public:
    explicit ExecutionScope(context_c* owner, PyObject* subject = nullptr) noexcept;
    explicit operator bool() const { return entered_; }
    v8::Isolate* isolate() const { return lock_->isolate(); }
    v8::Local<v8::Context> context() const { return context_; }
private:
    PyOwnedRef owner_;
    PyOwnedRef subject_;
    std::optional<IsolateLockScope> lock_;
    std::optional<v8::HandleScope> handles_;
    v8::Local<v8::Context> context_;
    std::optional<v8::Context::Scope> context_scope_;
    bool entered_ = false;
};

#endif
