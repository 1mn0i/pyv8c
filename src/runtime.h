#ifndef PYV8_RUNTIME_H
#define PYV8_RUNTIME_H

#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#endif

#include <v8.h>

#include "platform_state.h"

#include <memory>
#include <unordered_set>

class IsolateState;

class RuntimeManager
{
public:
    RuntimeManager();
    ~RuntimeManager();

    bool initialize(PyObject* icu, PyObject* params);
    bool shutdown(bool report_error = true);
    bool require_initialized() const;

    bool is_initialized() const;
    bool is_disposed() const { return platform_.is_disposed(); }

    IsolateState* default_isolate() const { return default_isolate_.get(); }
    IsolateState* create_isolate();
    bool destroy_isolate(IsolateState* state, bool report_error = true);
    IsolateState* find_isolate(v8::Isolate* isolate) const;

private:
    PlatformState platform_;
    std::unique_ptr<IsolateState> default_isolate_;
    std::unordered_set<IsolateState*> custom_isolates_;
};

extern RuntimeManager runtime_manager;

#endif
