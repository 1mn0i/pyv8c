#include "isolate_state.h"
#include "context.h"
#include "jsobject.h"
#include "pyv8.h"
#include "pyclass.h"
#include "v8_compat.h"
#include <cassert>
#include <new>
using namespace v8;

struct IsolateState::PythonObjectOwner
{
    explicit PythonObjectOwner(IsolateState* isolate_state)
        : isolate_state(isolate_state), context(nullptr), py_object(nullptr), exc_type(nullptr), exc_traceback(nullptr), wrapper(nullptr)
    {
    }

    IsolateState* isolate_state;
    context_c* context;
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

void IsolateState::drain_deferred() {
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    while (pending_wrappers_ || pending_owners_ || pending_context_closes) {
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

void IsolateState::invalidate_callbacks(context_c* context) {
    if (context->resources && context->resources->lifetime)
        context->resources->lifetime->owner = nullptr;
}

PyObject* IsolateState::callback_target(Local<Value> value) {
    auto* data = static_cast<CallbackData*>(v8_compat::external_value(value));
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
    assert(v8_strings_ != nullptr);
    return v8_strings_->get(isolate_, id);
}

bool
IsolateState::track_python_object(Local<Object> object, PyObject* py_object, context_c* context)
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

    // Published last, so the table never names an owner whose fields are not
    // filled in yet.  Failing to publish is a miss rather than an error: the pair
    // is made and works, JavaScript just gets a second object for this Python one
    // the next time it asks.  Nothing is retained either way, which is what keeps
    // the table safe to lose.
    try
    {
        owner->lifetime->js_object_cache[py_object] = owner;
    }
    catch (const std::bad_alloc&)
    {
        // Same as an entry that was never written.
    }
    return true;
}

IsolateState::PythonObjectOwner*
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
    return static_cast<PythonObjectOwner*>(item->second);
}

v8::Local<v8::Object>
IsolateState::cached_js_object(context_c* context, PyObject* py_object)
{
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
    // Asked for on every attribute read of a context, so an answer that is
    // already there must not cost a trip into the isolate: the table is keyed by
    // Python object and the handle is a Python object, and neither needs V8.  The
    // GIL is held throughout, and it is held by everyone who writes the table or
    // takes an entry out of it, which is what makes reading it here safe.
    auto* owner = find_python_owner(context, py_object);
    if (owner == nullptr)
        return nullptr;
    PyObject* wrapper = owner->wrapper;
    if (wrapper == nullptr)
    {
        // Building the first handle is the only part that needs the isolate, so
        // it is the only part that pays to enter it.
        IN_ISOLATE(this);
        // Entering released the GIL while it waited, so the pair may be gone, or
        // replaced by a newer one, by now: what was found before the wait cannot
        // be used after it.
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
            // Weak on purpose.  A handle that held the JavaScript object strongly
            // would hold its owner alive, and the owner is what holds the Python
            // object alive: one cycle and neither side would ever be collected
            // again.  V8 gives this reference back when the object dies, and the
            // context gives it back when it closes.
            wrapper = (PyObject*)js_object_weak_new(object, js_context);
            if (wrapper == nullptr)
                return nullptr;
            owner->wrapper = wrapper;
        }
    }
    Py_INCREF(wrapper);
    return wrapper;
}

bool
IsolateState::attach_exception_state(Local<Object> object,
                                     PyObject* exc_type,
                                     PyObject* exc_traceback)
{
    if (object->InternalFieldCount() <= PYTHON_OWNER_FIELD)
        return false;
    Local<Data> owner_value = object->GetInternalField(PYTHON_OWNER_FIELD);
    PythonObjectOwner* owner = static_cast<PythonObjectOwner*>(
        v8_compat::external_value(owner_value));
    if (owner == nullptr || owner->isolate_state != this)
        return false;

    Py_XINCREF((PyObject*)exc_type);
    Py_XINCREF((PyObject*)exc_traceback);
    Py_XDECREF(owner->exc_type);
    Py_XDECREF(owner->exc_traceback);
    owner->exc_type = exc_type;
    owner->exc_traceback = exc_traceback;

    return true;
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
        auto* owner = static_cast<PythonObjectOwner*>(*context->resources->python_owners.begin());
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
