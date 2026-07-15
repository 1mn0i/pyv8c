#ifndef PYV8_ISOLATE_SCOPE_H
#define PYV8_ISOLATE_SCOPE_H

#include <v8.h>

class IsolateState;

IsolateState* current_isolate_state();

class IsolateLockScope
{
public:
    explicit IsolateLockScope(IsolateState* state);
    ~IsolateLockScope();

    IsolateLockScope(const IsolateLockScope&) = delete;
    IsolateLockScope& operator=(const IsolateLockScope&) = delete;

    v8::Isolate* isolate() const { return isolate_; }

private:
    IsolateState* state_;
    IsolateState* previous_;
    v8::Isolate* isolate_;
    v8::Locker locker_;
    v8::Isolate::Scope isolate_scope_;
};

#endif
