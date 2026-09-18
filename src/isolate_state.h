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
#include <mutex>
#include <unordered_set>
#include <vector>

struct context_c;

class IsolateState
{
public:
    std::recursive_mutex entry_mutex;
    unsigned entry_depth = 0;
    PyObject* python_owner = nullptr;
    std::vector<PyObject*> inspector_waiters;
    std::vector<PyObject*> deferred_releases;
    std::vector<context_c*> pending_context_closes;
    void drain_deferred();
    void defer_release(PyObject* object);
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

    // The listeners are isolate-wide; Python reporters belong to each Context.
    bool error_hooks_installed() const { return error_hooks_installed_; }
    void set_error_hooks_installed(bool installed) { error_hooks_installed_ = installed; }

    PyObject* javascript_name();
    v8::Local<v8::String> v8_string(V8StringId id) const;

    bool track_python_object(v8::Local<v8::Object> object, PyObject* py_object, context_c* context);
    int traverse_context_owners(context_c* context, visitproc visit, void* arg);
    void release_context_owners(context_c* context);
    struct CallbackData {
        IsolateState* state;
        context_c* context;
        PyObject* target;
        // The Context owns target. This weak anchor may outlive its close.
        v8::Global<v8::External> external;
    };
    v8::Local<v8::External> callback_data(context_c* context, PyObject* target);
    void invalidate_callbacks(context_c* context);
    static PyObject* callback_target(v8::Local<v8::Value> data);
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
    PyObject* javascript_name_;
    bool error_hooks_installed_;
    std::unordered_set<PythonObjectOwner*> python_owners_;
    std::unordered_set<CallbackData*> callback_data_;
    static void callback_data_collected(const v8::WeakCallbackInfo<CallbackData>& info);
    bool disposed_;
};

#endif
