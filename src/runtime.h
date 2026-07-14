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
#include <libplatform/libplatform.h>

#include "v8_string_cache.h"

#include <cstddef>
#include <memory>
#include <unordered_set>

struct PythonObjectOwner;

class RuntimeState
{
public:
    RuntimeState();
    ~RuntimeState();

    bool initialize(PyObject* icu, PyObject* params);
    bool shutdown(bool report_error = true);
    bool require_initialized() const;

    bool is_initialized() const { return isolate != nullptr; }
    bool is_disposed() const { return disposed_; }

    void retain_v8_handle();
    void release_v8_handle();
    std::size_t live_v8_handles() const { return live_v8_handles_; }

    PyObject* function_template_cache();
    void clear_function_template_cache();
    PyObject* javascript_name();
    v8::Local<v8::String> v8_string(V8StringId id) const;

    bool track_python_object(v8::Local<v8::Object> object, PyObject* py_object);
    bool attach_exception_state(v8::Local<v8::Object> object,
                                PyObject* exc_type,
                                PyObject* exc_traceback);
    void release_python_owner(PythonObjectOwner* owner);

    v8::Isolate* isolate;

private:
    void clear_python_owners();

    std::unique_ptr<v8::Platform> platform_;
    std::unique_ptr<v8::ArrayBuffer::Allocator> allocator_;
    std::unique_ptr<V8StringCache> v8_strings_;
    std::size_t live_v8_handles_;
    PyObject* function_template_cache_;
    PyObject* javascript_name_;
    std::unordered_set<PythonObjectOwner*> python_owners_;
    bool disposed_;
};

extern RuntimeState runtime_state;

#endif
