#ifndef PYV8_ISOLATE_STATE_H
#define PYV8_ISOLATE_STATE_H

#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#endif

#include <v8.h>

#include "v8_string_cache.h"

#include <cstddef>
#include <memory>
#include <unordered_set>

class IsolateState
{
public:
    IsolateState();
    ~IsolateState();

    bool initialize();
    bool shutdown(bool report_error = true);
    bool require_active() const;

    bool is_active() const { return isolate_ != nullptr; }
    bool is_disposed() const { return disposed_; }
    v8::Isolate* isolate() const { return isolate_; }

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

private:
    struct PythonObjectOwner;

    static void python_object_weak_callback(
        const v8::WeakCallbackInfo<PythonObjectOwner>& info);
    void release_python_owner(PythonObjectOwner* owner);
    void clear_python_owners();

    std::unique_ptr<v8::ArrayBuffer::Allocator> allocator_;
    std::unique_ptr<V8StringCache> v8_strings_;
    v8::Isolate* isolate_;
    std::size_t live_v8_handles_;
    PyObject* function_template_cache_;
    PyObject* javascript_name_;
    std::unordered_set<PythonObjectOwner*> python_owners_;
    bool disposed_;
};

#endif
