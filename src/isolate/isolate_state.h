#ifndef PYV8_ISOLATE_STATE_H
#define PYV8_ISOLATE_STATE_H

#include "bridge_base.h"
#include <Python.h>

#ifdef _WIN32
#undef COMPILER
#ifndef NO_RETURN
#define NO_RETURN __declspec(noreturn)
#endif
#endif

#include <v8.h>
#include <v8-external-memory-accounter.h>

#include "v8_string_cache.h"
#include "watchdog.h"

#include <cstddef>
#include <memory>
#include <mutex>
#include <unordered_set>
#include <vector>

struct context_c;
struct ContextLifetime;
// The JavaScript object standing for a Python host object in one realm, plus
// the Python references that pair keeps.  Defined beside the pair table.
struct PythonObjectOwner;
struct DeferredPythonRelease {
    PyObject* object;
    DeferredPythonRelease* next;
};

class IsolateState
{
private:
    friend class IsolateLockScope;
    std::recursive_mutex entry_mutex;
    unsigned entry_depth = 0;
    unsigned entry_users = 0; // GIL protected; includes threads waiting for entry.
    PyObject* python_owner = nullptr; // borrowed from the Python Isolate owner
    std::vector<PyObject*> inspector_waiters;
    context_c* pending_context_closes = nullptr;
public:
    void bind_python_owner(PyObject* owner) { python_owner = owner; }
    bool register_inspector_waiter(PyObject* callback);
    bool defer_context_close(context_c* context);
    void drain_deferred();
    void defer_release(DeferredPythonRelease* record, PyObject* object);
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

    // Python memory held for JavaScript; V8 cannot see it, so it is reported.
    void account_external_memory(int64_t delta);
    int64_t external_memory() const { return external_memory_bytes_; }

    Watchdog& watchdog() { return *watchdog_; }

    // The listeners are isolate-wide; Python reporters belong to each Context.
    bool error_hooks_installed() const { return error_hooks_installed_; }
    void set_error_hooks_installed(bool installed) { error_hooks_installed_ = installed; }

    v8::Local<v8::String> v8_string(V8StringId id) const;

    bool track_python_object(v8::Local<v8::Object> object, PyObject* py_object, context_c* context);
    // Release the owner the old realm published; the object is left standing.
    bool transfer_python_object(v8::Local<v8::Object> object);
    // The JavaScript object that already stands for *py_object* in *context*;
    // wrapping one Python object twice would give JavaScript two objects where it
    // can only tell one apart.  Empty means "not wrapped here yet".
    v8::Local<v8::Object> cached_js_object(context_c* context, PyObject* py_object);
    // The Python handle for that same pair, built on first use: a new reference, or
    // null with no error set when the pair is not known here.
    PyObject* cached_js_wrapper(context_c* context, PyObject* py_object);
    static PyObject* python_object(v8::Local<v8::Object> object);
    static void restore_python_exception(v8::Local<v8::Object> object);
    int traverse_context_owners(context_c* context, visitproc visit, void* arg);
    void release_context_owners(context_c* context);
    struct CallbackData {
        IsolateState* state;
        std::shared_ptr<ContextLifetime> lifetime;
        Py_ssize_t target_index;
        // The Context owns target. This weak anchor may outlive its close, so it
        // is uncounted for the same reason PythonObjectOwner's object is.
        v8::Global<v8::External> external;
    };
    v8::Local<v8::External> callback_data(context_c* context, PyObject* target);
    void invalidate_callbacks(context_c* context);
    static PyObject* callback_target(v8::Local<v8::Value> data);

    // A failure of the host's own, which no realm may be shown: it ends the crossing
    // and waits here for the Python boundary that asked for it.
    void hold_aborted_failure(PyObject* exc_type, PyObject* exc_value, PyObject* exc_traceback);
    bool resume_aborted_failure();
    bool has_aborted_failure() const { return aborted_value_ != nullptr; }
    void clear_aborted_failure();

private:
    static PythonObjectOwner* find_python_owner(context_c* context, PyObject* py_object);

    // Growth near the heap limit: one step per call, capped.  Both are fractions of
    // the limit V8 started with, so --max-old-space-size still decides the scale.
    static constexpr std::size_t kHeapGrowthStepDivisor = 8;
    static constexpr std::size_t kHeapGrowthCapDivisor = 2;
    static std::size_t near_heap_limit_callback(void* data, std::size_t current_limit,
                                                std::size_t initial_limit);
    std::size_t heap_growth_granted_;

    v8::ExternalMemoryAccounter external_memory_;
    int64_t external_memory_bytes_;
    PythonObjectOwner* pending_owners_ = nullptr;
    DeferredPythonRelease* pending_wrappers_ = nullptr;

    static void python_object_weak_callback(
        const v8::WeakCallbackInfo<PythonObjectOwner>& info);
    void release_python_owner(PythonObjectOwner* owner);
    void clear_python_owners();

    std::unique_ptr<v8::ArrayBuffer::Allocator> allocator_;
    std::unique_ptr<V8StringCache> v8_strings_;
    std::unique_ptr<Watchdog> watchdog_;
    v8::Isolate* isolate_;
    std::size_t live_v8_handles_;
    bool error_hooks_installed_;
    PyObject* aborted_type_;
    PyObject* aborted_value_;
    PyObject* aborted_traceback_;
    std::unordered_set<PythonObjectOwner*> python_owners_;
    std::unordered_set<CallbackData*> callback_data_;
    static void callback_data_collected(const v8::WeakCallbackInfo<CallbackData>& info);
    bool disposed_;
};

#endif
