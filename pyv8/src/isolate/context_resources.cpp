#include "bridge_base.h"
#include "isolate_state.h"
#include "context.h"
#include "jsobject.h"
#include "entry.h"
#include "v8_compat.h"
#include <cassert>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <new>
using namespace v8;

namespace
{

// Nominal cost of one wrapped host object: the stable ABI cannot size an arbitrary
// object, so this is an estimate rather than a measurement.
constexpr int64_t kHostObjectExternalBytes = 256;

}

struct PythonObjectOwner
{
    explicit PythonObjectOwner(IsolateState* isolate_state)
        : isolate_state(isolate_state), context(nullptr), py_object(nullptr), exc_type(nullptr), exc_traceback(nullptr), wrapper(nullptr)
    {
    }

    IsolateState* isolate_state;
    context_c* context;
    // Weak and deliberately uncounted: owners are released in the teardown that
    // runs after the handle count reached zero.
    Global<Object> object;
    PyObject* py_object;
    PyObject* exc_type;
    PyObject* exc_traceback;
    // Shared with the context so the pair table stays reachable -- and writable --
    // even while the context is being torn down.
    std::shared_ptr<ContextLifetime> lifetime;
    // The Python handle this pair hands to Python, built the first time one is
    // asked for.  Borrowed here: the one reference it holds belongs to V8's weak
    // callback, which gives it back when the JavaScript object dies, or to the
    // context, which gives it back when it closes.
    PyObject* wrapper;
    PythonObjectOwner* next_pending = nullptr;
};

void
IsolateState::python_object_weak_callback(
    const WeakCallbackInfo<PythonObjectOwner>& info)
{
    PythonObjectOwner* owner = info.GetParameter();
    owner->object.Reset();
    owner->isolate_state->release_python_owner(owner);
}

void IsolateState::defer_release(DeferredPythonRelease* record, PyObject* object) {
    record->object = object;
    record->next = pending_wrappers_;
    pending_wrappers_ = record;
}

bool IsolateState::register_inspector_waiter(PyObject* callback) {
    if (std::find(inspector_waiters.begin(), inspector_waiters.end(), callback) != inspector_waiters.end())
        return true;
    try { inspector_waiters.push_back(callback); }
    catch (const std::bad_alloc&) { PyErr_NoMemory(); return false; }
    Py_INCREF(callback);
    return true;
}

bool IsolateState::defer_context_close(context_c* context) {
    if (entry_depth <= 1) return false;
    Py_INCREF((PyObject*)context);
    context->next_pending_close = pending_context_closes;
    pending_context_closes = context;
    return true;
}

void IsolateState::defer_cleanup(PyObject* object, int (*clear)(PyObject*)) {
    for (const auto& pending : pending_cleanups)
        if (pending.object == object) return;
    pending_cleanups.push_back({object, clear});
    Py_INCREF(object);
}

void IsolateState::drain_deferred() {
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    while (pending_wrappers_ || pending_owners_ || pending_context_closes || !pending_cleanups.empty()) {
        while (!pending_cleanups.empty()) {
            const auto pending = pending_cleanups.back();
            pending_cleanups.pop_back();
            if (pending.clear(pending.object) < 0) PyErr_WriteUnraisable(pending.object);
            Py_DECREF(pending.object);
        }
        while (pending_wrappers_) {
            auto* record = pending_wrappers_;
            pending_wrappers_ = record->next;
            auto* object = record->object;
            record->object = nullptr;
            record->next = nullptr;
            Py_DECREF(object);
        }
        while (pending_owners_) {
            auto* owner = pending_owners_;
            pending_owners_ = owner->next_pending;
            Py_XDECREF(owner->py_object);
            Py_XDECREF(owner->exc_type);
            Py_XDECREF(owner->exc_traceback);
            delete owner;
            // The references go away here, so the report does too.
            account_external_memory(-kHostObjectExternalBytes);
        }
        while (pending_context_closes) {
            auto* context = pending_context_closes;
            pending_context_closes = context->next_pending_close;
            context->next_pending_close = nullptr;
            context_finish_close(context);
            Py_DECREF((PyObject*)context);
        }
    }
    PyErr_Restore(type, value, traceback);
}

Local<External> IsolateState::callback_data(context_c* context, PyObject* target) {
    CallbackData* data = nullptr;
    try {
        if (!context->resources) context->resources = new ContextResources();
        auto* resources = context->resources;
        if (!resources->lifetime) resources->lifetime = std::make_shared<ContextLifetime>(context);
        if (!context->callback_targets) context->callback_targets = PyList_New(0);
        if (!context->callback_targets) return {};
        data = new CallbackData{this, resources->lifetime, PyList_Size(context->callback_targets), {}};
        callback_data_.insert(data);
        resources->callbacks.insert(data);
        if (PyList_Append(context->callback_targets, target) < 0) {
            callback_data_.erase(data);
            resources->callbacks.erase(data);
            delete data;
            return {};
        }
        auto external = v8_compat::external_new(isolate_, data);
        data->external.Reset(isolate_, external);
        data->external.SetWeak(data, callback_data_collected, WeakCallbackType::kParameter);
        return external;
    } catch (const std::bad_alloc&) {
        callback_data_.erase(data);
        if (context->resources) context->resources->callbacks.erase(data);
        delete data;
        PyErr_NoMemory();
        return {};
    }
}

void IsolateState::callback_data_collected(const WeakCallbackInfo<CallbackData>& info) {
    auto* data = info.GetParameter();
    data->external.Reset();
    auto* context = data->lifetime->owner;
    if (context && context->resources) context->resources->callbacks.erase(data);
    data->state->callback_data_.erase(data);
    delete data;
}

bool IsolateState::shared_templates_enabled() const {
    static const bool enabled = [] {
        const char* flag = getenv("PYV8C_SHARED_TEMPLATES");
        return flag == nullptr || strcmp(flag, "0") != 0;
    }();
    return enabled;
}

Local<External> IsolateState::shared_callback_data(PyObject* target) {
    if (target == nullptr) return {};
    CallbackData* data = nullptr;
    try {
        // No ContextLifetime: the interface template that holds this callback lives
        // as long as the isolate, not as long as the realm that made it first.
        data = new CallbackData{this, nullptr, -1, PyV8_NewRef(target), {}};
        callback_data_.insert(data);
        auto external = v8_compat::external_new(isolate_, data);
        data->external.Reset(isolate_, external);
        data->external.SetWeak(data, shared_callback_collected, WeakCallbackType::kParameter);
        return external;
    } catch (const std::bad_alloc&) {
        if (data != nullptr) {
            callback_data_.erase(data);
            Py_XDECREF(data->owned_target);
            delete data;
        }
        PyErr_NoMemory();
        return {};
    }
}

void IsolateState::shared_callback_collected(const WeakCallbackInfo<CallbackData>& info) {
    auto* data = info.GetParameter();
    data->external.Reset();
    data->state->callback_data_.erase(data);
    Py_XDECREF(data->owned_target);
    delete data;
}

PyObject* IsolateState::shared_interface(PyObject* cls) {
    auto found = shared_interfaces_.find(cls);
    return found == shared_interfaces_.end() ? nullptr : found->second;
}

void IsolateState::remember_shared_interface(PyObject* cls, PyObject* templ) {
    Py_INCREF(cls);
    Py_INCREF(templ);
    shared_interfaces_[cls] = templ;
}

void IsolateState::invalidate_callbacks(context_c* context) {
    if (context->resources && context->resources->lifetime)
        context->resources->lifetime->owner = nullptr;
}

PyObject* IsolateState::callback_target(Local<Value> value) {
    auto* data = static_cast<CallbackData*>(v8_compat::external_value(value));
    if (data != nullptr && data->owned_target != nullptr)
        return data->owned_target;
    auto* owner = data ? data->lifetime->owner : nullptr;
    if (!context_is_active(owner)) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is closed");
        return nullptr;
    }
    return PyList_GetItem(owner->callback_targets, data->target_index);
}

Local<String>
IsolateState::v8_string(V8StringId id) const
{
    // An isolate being taken apart has no cache left, and an empty handle
    // compares unequal to every magic value, which is the right answer.
    if (v8_strings_ == nullptr || isolate_ == nullptr)
        return Local<String>();
    return v8_strings_->get(isolate_, id);
}

bool
IsolateState::track_python_object(Local<Object> object, PyObject* py_object,
                                  context_c* context, bool share_wrapper)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
    {
        PyErr_SetString(PyExc_RuntimeError,
                        "JavaScript object has no Python ownership field");
        return false;
    }

    PythonObjectOwner* owner = new (std::nothrow) PythonObjectOwner(this);
    if (owner == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }

    Py_INCREF((PyObject*)py_object);
    owner->context = context;
    owner->py_object = py_object;
    try
    {
        python_owners_.insert(owner);
        if (!context->resources) context->resources = new ContextResources();
        auto* resources = context->resources;
        if (!resources->lifetime) resources->lifetime = std::make_shared<ContextLifetime>(context);
        resources->python_owners.insert(owner);
        owner->lifetime = resources->lifetime;
    }
    catch (const std::bad_alloc&)
    {
        python_owners_.erase(owner);
        if (context->resources) context->resources->python_owners.erase(owner);
        Py_DECREF((PyObject*)py_object);
        delete owner;
        PyErr_NoMemory();
        return false;
    }
    owner->object.Reset(isolate_, object);
    owner->object.SetWeak(owner, python_object_weak_callback,
                          WeakCallbackType::kParameter);
    object->SetInternalField(PYTHON_OWNER_FIELD, v8_compat::external_new(isolate_, owner));

    // Published last, so the table never names an owner whose fields are unset.
    // Failing to publish is only a miss: the pair works, JavaScript just gets a
    // second object for this Python one next time.
    try
    {
        owner->lifetime->js_object_cache[py_object] = owner;
    }
    catch (const std::bad_alloc&)
    {
        // Same as an entry that was never written.
    }
    // An IDL host object keeps its wrapper when the same node is read from another
    // same-origin realm. A plain Python object stays realm-local: sharing it would
    // carry JavaScript properties, and their writes, across unrelated realms.
    if (share_wrapper && shared_owner(py_object) == nullptr)
        remember_shared_owner(py_object, owner);
    // Counted last: the paths above that fail take the owner with them.
    account_external_memory(kHostObjectExternalBytes);
    return true;
}

bool
IsolateState::transfer_python_object(Local<Object> object)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
    {
        PyErr_SetString(PyExc_TypeError,
                        "reuse_global expects a realm's global object");
        return false;
    }
    Local<Data> magic = object->GetInternalField(PYTHON_MAGIC_FIELD);
    if (magic != v8_string(V8StringId::PythonObjectMagic))
    {
        PyErr_SetString(PyExc_TypeError,
                        "reuse_global expects a realm's global object");
        return false;
    }
    auto* owner = static_cast<PythonObjectOwner*>(
        v8_compat::external_value(object->GetInternalField(PYTHON_OWNER_FIELD)));
    if (owner == nullptr)
    {
        PyErr_SetString(PyExc_TypeError,
                        "reuse_global expects a realm's global object");
        return false;
    }
    // The weak handle goes with the owner, which the deferred release frees.
    owner->object.Reset();
    release_python_owner(owner);
    // Cleared: a reader in between must find nothing, not a released owner.
    Local<External> cleared = v8_compat::external_new(isolate_, nullptr);
    object->SetInternalField(PYTHON_MAGIC_FIELD, cleared);
    object->SetInternalField(PYTHON_OWNER_FIELD, cleared);
    return true;
}

PythonObjectOwner*
IsolateState::find_python_owner(context_c* context, PyObject* py_object)
{
    if (context == nullptr || py_object == nullptr)
        return nullptr;
    ContextResources* resources = context->resources;
    if (resources == nullptr || resources->lifetime == nullptr)
        return nullptr;

    auto& cache = resources->lifetime->js_object_cache;
    auto item = cache.find(py_object);
    if (item == cache.end())
        return nullptr;
    return item->second;
}

PythonObjectOwner* IsolateState::shared_owner(PyObject* py_object) {
    auto found = shared_wrappers_.find(py_object);
    return found == shared_wrappers_.end() ? nullptr : found->second;
}

void IsolateState::remember_shared_owner(PyObject* py_object, PythonObjectOwner* owner) {
    try {
        shared_wrappers_[py_object] = owner;
    } catch (const std::bad_alloc&) {
        // An entry that was never written: the pair still works, identity just
        // stays realm-local for this object.
    }
}

void IsolateState::forget_shared_owner(PythonObjectOwner* owner) {
    auto found = shared_wrappers_.find(owner->py_object);
    if (found != shared_wrappers_.end() && found->second == owner)
        shared_wrappers_.erase(found);
}

v8::Local<v8::Object>
IsolateState::cached_js_object(context_c* context, PyObject* py_object)
{
    if (PythonObjectOwner* shared = shared_owner(py_object)) {
        if (!shared->object.IsEmpty())
            return shared->object.Get(isolate_);
        forget_shared_owner(shared);
    }
    auto* owner = find_python_owner(context, py_object);
    if (owner == nullptr || owner->object.IsEmpty())
        return v8::Local<v8::Object>();
    // A weak handle keeps its object alive until V8 has run the callback that
    // releases the owner, and that callback erases this entry in the same step,
    // so what is read here is either the live object or nothing at all.
    return owner->object.Get(isolate_);
}

PyObject*
IsolateState::cached_js_wrapper(context_c* context, PyObject* py_object)
{
    // Asked for on every attribute read of a context, so the answer when it is
    // already there must not cost a trip into the isolate.  The GIL is held
    // throughout, by readers and writers alike.
    auto* owner = find_python_owner(context, py_object);
    if (owner == nullptr)
        return nullptr;
    PyObject* wrapper = owner->wrapper;
    if (wrapper == nullptr)
    {
        // Only building the first handle needs the isolate, and entering released
        // the GIL while it waited, so the pair is looked up again.
        IN_ISOLATE(this);
        owner = find_python_owner(context, py_object);
        if (owner == nullptr)
            return nullptr;
        wrapper = owner->wrapper;
        if (wrapper == nullptr)
        {
            Local<Object> object = owner->object.Get(isolate);
            Local<Context> js_context = context->js_context.Get(isolate);
            if (object.IsEmpty() || js_context.IsEmpty())
                return nullptr;
            // Weak on purpose: a strong handle would hold its owner alive, and the
            // owner holds the Python object -- a cycle neither side would leave.
            wrapper = (PyObject*)js_object_weak_new(object, js_context);
            if (wrapper == nullptr)
                return nullptr;
            owner->wrapper = wrapper;
        }
    }
    Py_INCREF(wrapper);
    return wrapper;
}

PyObject* IsolateState::python_object(Local<Object> object) {
    auto* owner = static_cast<PythonObjectOwner*>(
        v8_compat::external_value(object->GetInternalField(PYTHON_OWNER_FIELD)));
    if (!owner || !context_is_active(owner->context)) {
        PyErr_SetString(PyExc_RuntimeError, "host object Context is closed");
        return nullptr;
    }
    return owner->py_object;
}

PyObject* IsolateState::python_object_if_owned(Local<Object> object) {
    // An arbitrary V8 object may have internal fields of unrelated types.
    // Compare known handles before reading one as our PythonOwner pointer.
    for (auto* owner : python_owners_) {
        if (!owner->object.IsEmpty() && owner->object.Get(isolate_) == object &&
            context_is_active(owner->context))
            return owner->py_object;
    }
    return nullptr;
}

void IsolateState::restore_python_exception(Local<Object> object) {
    auto* value = python_object(object);
    if (!value) return;
    auto* owner = static_cast<PythonObjectOwner*>(
        v8_compat::external_value(object->GetInternalField(PYTHON_OWNER_FIELD)));
    if (!owner->exc_type && !owner->exc_traceback) {
        PyErr_SetObject((PyObject*)Py_TYPE(value), value);
    } else {
        Py_XINCREF(owner->exc_type);
        Py_INCREF(value);
        Py_XINCREF(owner->exc_traceback);
        PyErr_Restore(owner->exc_type, value, owner->exc_traceback);
    }
}

void
IsolateState::release_python_owner(PythonObjectOwner* owner)
{
    forget_shared_owner(owner);
    if (owner->lifetime != nullptr && owner->py_object != nullptr)
    {
        auto& cache = owner->lifetime->js_object_cache;
        auto item = cache.find(owner->py_object);
        // Only the entry this owner published.  A later wrap of the same Python
        // object -- which the death of the first one's JavaScript object makes
        // possible -- replaces it, and releasing the older owner must not take
        // the newer one's entry away.
        if (item != cache.end() && item->second == owner)
            cache.erase(item);
    }
    python_owners_.erase(owner);
    if (owner->context && owner->context->resources)
        owner->context->resources->python_owners.erase(owner);
    owner->context = nullptr;
    owner->next_pending = pending_owners_;
    pending_owners_ = owner;
}

int IsolateState::traverse_context_owners(context_c* context, visitproc visit, void* arg) {
    if (!context->resources) return 0;
    for (auto* item : context->resources->python_owners) {
        auto* owner = static_cast<PythonObjectOwner*>(item);
        Py_VISIT(owner->py_object);
        Py_VISIT(owner->exc_type);
        Py_VISIT(owner->exc_traceback);
    }
    return 0;
}

void IsolateState::release_context_owners(context_c* context) {
    if (!context->resources) return;
    // Allocate before iteration: V8 GC can remove other owners from the registry.
    auto cleared = v8_compat::external_new(isolate_, nullptr);
    while (!context->resources->python_owners.empty()) {
        auto* owner = *context->resources->python_owners.begin();
        if (!owner->object.IsEmpty()) {
            auto object = owner->object.Get(isolate_);
            for (int field = 0; field < OBJECT_INTERNAL_FIELDS; ++field)
                object->SetInternalField(field, cleared);
            owner->object.Reset();
        }
        release_python_owner(owner);
    }
}

void
IsolateState::clear_python_owners()
{
    while (!python_owners_.empty())
    {
        PythonObjectOwner* owner = *python_owners_.begin();
        owner->object.Reset();
        release_python_owner(owner);
    }
}
