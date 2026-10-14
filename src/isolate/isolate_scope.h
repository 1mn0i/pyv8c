#ifndef PYV8_ISOLATE_SCOPE_H
#define PYV8_ISOLATE_SCOPE_H

#include "bridge_base.h"
#include <memory>

#include <v8.h>
#include <Python.h>

class IsolateState;

IsolateState* current_isolate_state();

class IsolateLockScope
{
public:
    explicit IsolateLockScope(IsolateState* state, bool try_only = false);
    ~IsolateLockScope();

    IsolateLockScope(const IsolateLockScope&) = delete;
    IsolateLockScope& operator=(const IsolateLockScope&) = delete;

    v8::Isolate* isolate() const { return isolate_; }
    bool entered() const { return entered_; }

private:
    IsolateState* state_;
    IsolateState* previous_;
    v8::Isolate* isolate_;
    std::unique_ptr<v8::Locker> locker_;
    bool entered_;
    bool nested_;
    PyObject* python_owner_;
};

#endif
