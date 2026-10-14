#include "bridge_base.h"
#include <cmath>
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "runtime.h"
#include "crossing.h"
#include <v8.h>
#include <algorithm>

#include "context.h"
#include "debugger.h"
#include "error_hooks.h"
#include "idl_spec.h"
#include "script.h"
#include "convert.h"
#include "jsobject.h"
#include "jsvalue.h"
#include "pyclass.h"
#include "v8tools.h"
#include "isolate.h"
#include "watchdog.h"

using namespace v8;

static PyObject* context_try_inspector(context_c* self, PyObject* args);
static PyObject* context_to_js(context_c* self, PyObject* value);
static PyObject* context_import_handle(context_c* self, PyObject* handle);
static PyObject* context_set_deadline(context_c* self, PyObject* args, PyObject* kwargs);
static PyObject* context_clear_deadline(context_c* self, PyObject* ignored);
static PyObject* context_terminate(context_c* self, PyObject* ignored);
static int context_clear(context_c* self);
static int context_traverse(context_c* self, visitproc visit, void* arg);

static bool valid_timeout(double seconds) {
    const double maximum = std::chrono::duration<double>(
        std::chrono::steady_clock::time_point::max() - std::chrono::steady_clock::now()).count();
    if (!std::isfinite(seconds) || seconds > maximum) {
        PyErr_SetString(PyExc_ValueError, "timeout must be finite and fit the monotonic clock");
        return false;
    }
    return true;
}

bool context_is_active(context_c* self) {
    return self != nullptr && self->state == ContextState::Active && self->isolate_state != nullptr &&
        !self->js_context.IsEmpty();
}

context_c* context_from_v8(Local<Context> context) {
    if (context.IsEmpty() || context->GetNumberOfEmbedderDataFields() <= CONTEXT_OBJECT_SLOT)
        return nullptr;
    auto* lifetime = v8_compat::context_embedder_pointer<ContextLifetime>(context, CONTEXT_OBJECT_SLOT);
    return lifetime ? lifetime->owner : nullptr;
}

bool context_is_detached(Local<Context> context) {
    context_c* owner = context_from_v8(context);
    return owner != nullptr && owner->detached;
}

PyObject* context_detach(context_c* self, PyObject* ignored) {
    (void)ignored;
    //: Left behind: still readable, but nothing new is built inside it.
    self->detached = true;
    // Nothing new is asked of the embedder for a realm that no longer has a document.
    Py_CLEAR(self->lazy_factory);
    PYV8_RETURN_NONE;
}

// Whether an intercepted access may proceed.  The accepting path costs a few
// reads and a state check, no allocation and no string.
bool context_check_callback(Local<Context> context) {
    // Context::New may call interceptors before embedder data is installed.
    if (!context.IsEmpty() &&
        (context->GetNumberOfEmbedderDataFields() <= CONTEXT_OBJECT_SLOT ||
         !context->GetEmbedderData(CONTEXT_OBJECT_SLOT)->IsExternal())) return true;
    auto* owner = context.IsEmpty() ? nullptr :
        context_from_v8(context);
    if (context_is_active(owner)) return true;
    auto* isolate = Isolate::GetCurrent();
    isolate->ThrowException(Exception::Error(V8String(isolate, "V8 context is closed")));
    return false;
}

void context_register_handle(context_c* self, PyObject* handle, int (*clear)(PyObject*)) {
    if (self->resources == nullptr) self->resources = new ContextResources();
    self->resources->handles[handle] = clear;
}

void context_unregister_handle(context_c* self, PyObject* handle) {
    if (self != nullptr && self->resources != nullptr) self->resources->handles.erase(handle);
}

bool context_retain_dependency(Local<Context> target, PyObject* source) {
    auto* owner = context_from_v8(target);
    if (source == nullptr || owner == nullptr || source == (PyObject*)owner) return true;
    auto* origin = (context_c*)source;
    // Already recorded?  The reverse index knows, so the list below does not have
    // to be searched to answer that: the list is there to hold the strong
    // references that keep the origins alive, and reading through it turned a
    // second and third window into a scan of the first.
    if (owner->resources != nullptr && owner->resources->dependencies.count(origin) != 0)
        return true;
    if (owner->dependencies == nullptr) owner->dependencies = PyList_New(0);
    if (owner->dependencies == nullptr) return false;
    try {
        if (origin->resources == nullptr) origin->resources = new ContextResources();
        if (owner->resources == nullptr) owner->resources = new ContextResources();
        origin->resources->dependents.insert(owner);
        owner->resources->dependencies.insert(origin);
        owner->resources->dependency_slots[origin] = PyList_Size(owner->dependencies);
    } catch (const std::bad_alloc&) {
        if (origin->resources) origin->resources->dependents.erase(owner);
        if (owner->resources) {
            owner->resources->dependencies.erase(origin);
            owner->resources->dependency_slots.erase(origin);
        }
        PyErr_NoMemory();
        return false;
    }
    if (PyList_Append(owner->dependencies, source) < 0) {
        origin->resources->dependents.erase(owner);
        owner->resources->dependencies.erase(origin);
        owner->resources->dependency_slots.erase(origin);
        return false;
    }
    return true;
}

// Drops *origin* from *owner*'s reference list, by swapping the last entry into
// its place: the list is the strong references, and the slot map says where the
// entry is, so neither lookup nor removal walks it.
void ContextResources::drop_dependency(context_c* owner, context_c* origin) {
    if (owner->dependencies == nullptr || owner->resources == nullptr)
        return;
    auto slot = owner->resources->dependency_slots.find(origin);
    if (slot == owner->resources->dependency_slots.end())
        return;
    const Py_ssize_t index = slot->second;
    const Py_ssize_t last = PyList_Size(owner->dependencies) - 1;
    owner->resources->dependency_slots.erase(slot);
    if (index < 0 || index > last)
        return;
    if (index != last) {
        PyObject* moved = PyList_GetItem(owner->dependencies, last);
        if (moved != nullptr) {
            Py_INCREF(moved);
            // Releasing the entry it replaces is the reference this drops.
            if (PyList_SetItem(owner->dependencies, index, moved) == 0)
                owner->resources->dependency_slots[(context_c*)moved] = index;
            else
                Py_DECREF(moved);
        }
    }
    PySequence_DelItem(owner->dependencies, last);
}

void ContextResources::unlink_dependencies(context_c* self) {
    if (self->resources != nullptr) {
        auto& dependents = self->resources->dependents;
        while (!dependents.empty()) {
            auto* target = *dependents.begin();
            dependents.erase(target);
            if (target->resources != nullptr) target->resources->dependencies.erase(self);
            if (target->resources) target->resources->drop_dependency(target, self);
        }
        for (auto* origin : self->resources->dependencies) {
            if (origin->resources != nullptr) origin->resources->dependents.erase(self);
        }
        self->resources->dependencies.clear();
        self->resources->dependency_slots.clear();
    }
    Py_CLEAR(self->dependencies);
}

static PyObject* context_heap_statistics(context_c* self, PyObject* args);

PyMethodDef context_methods[] = {
    {"_try_inspector", (PyCFunction)PythonBoundary<context_try_inspector>::call, METH_VARARGS, NULL},
    {"exec_js", (PyCFunction)PythonBoundary<context_run>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"js_obj", (PyCFunction)PythonBoundary<context_js_obj>::call, METH_O, NULL},
    {"expose", (PyCFunction)PythonBoundary<context_expose>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"hide_global", (PyCFunction)PythonBoundary<context_hide_global>::call, METH_O,
     "remove a name this realm exposed, as if it had never been installed"},
    {"define_global", (PyCFunction)PythonBoundary<context_define_global>::call, METH_VARARGS | METH_KEYWORDS,
     "install one global value with the property attributes the caller names"},
    {"install", (PyCFunction)PythonBoundary<context_install>::call, METH_O,
     "install each name as a non-enumerable lazy constructor"},
    {"gc", (PyCFunction)PythonBoundary<context_gc>::call, METH_NOARGS, NULL},
    {"heap_statistics", (PyCFunction)PythonBoundary<context_heap_statistics>::call, METH_NOARGS,
     "bytes of this isolate's heap in use, committed, and its ceiling, as a dict"},
    {"enqueue_microtask", (PyCFunction)PythonBoundary<context_enqueue_microtask>::call, METH_O, NULL},
    {"set_error_hooks", (PyCFunction)PythonBoundary<context_set_error_hooks>::call,
     METH_VARARGS | METH_KEYWORDS, NULL},
    {"close", (PyCFunction)PythonBoundary<context_close>::call, METH_NOARGS, NULL},
    {"detach", (PyCFunction)PythonBoundary<context_detach>::call, METH_NOARGS,
     "mark the realm as left behind by a navigation"},
    {"to_js", (PyCFunction)PythonBoundary<context_to_js>::call, METH_O, NULL},
    {"import_handle", (PyCFunction)PythonBoundary<context_import_handle>::call, METH_O, NULL},
    {"set_deadline", (PyCFunction)PythonBoundary<context_set_deadline>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"clear_deadline", (PyCFunction)PythonBoundary<context_clear_deadline>::call, METH_NOARGS, NULL},
    {"terminate", (PyCFunction)PythonBoundary<context_terminate>::call, METH_NOARGS, NULL},
    {NULL},
};

static IsolateState*
context_require_isolate(context_c* self)
{
    if (!context_is_active(self) || !self->isolate_state->is_active())
    {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is no longer active");
        return nullptr;
    }
    return self->isolate_state;
}
static PyObject* context_try_inspector(context_c* self, PyObject* args) {
    PyObject *callback, *wake;
    if (!PyArg_ParseTuple(args, "OO", &callback, &wake)) return nullptr;
    if (!PyCallable_Check(callback) || !PyCallable_Check(wake)) {
        PyErr_SetString(PyExc_TypeError, "Inspector callbacks must be callable");
        return nullptr;
    }
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    IsolateLockScope scope(state, true);
    if (!scope.entered()) {
        if (!state->register_inspector_waiter(wake)) return nullptr;
        Py_RETURN_FALSE;
    }
    PyObject* result = PyObject_CallNoArgs(callback);
    if (!result) return nullptr;
    Py_DECREF((PyObject*)result);
    Py_RETURN_TRUE;
}
static PyObject* context_isolate_id(context_c* self, void*) {
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    return PyLong_FromVoidPtr(state->isolate());
}
static PyObject* context_get_detached(context_c* self, void*) {
    return PyBool_FromLong(self->detached ? 1 : 0);
}
// The Python wrappers this realm still owns; zero once it has released them.
static PyObject* context_live_handles(context_c* self, void*) {
    auto* resources = self->resources;
    return PyLong_FromSsize_t(resources == nullptr ? 0
                              : static_cast<Py_ssize_t>(resources->handle_count()));
}
// How many crossings stopped because the heap was full; the limit itself is
// recovered from: the next crossing resumes the isolate.
static PyObject* context_heap_terminations(context_c* self, void*) {
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    return PyLong_FromSize_t(state->watchdog().heap_terminations());
}
static PyObject* context_heap_statistics(context_c* self, PyObject* args) {
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    v8::HeapStatistics statistics;
    state->isolate()->GetHeapStatistics(&statistics);
    return Py_BuildValue("{s:L,s:L,s:L}",
                         "used", (long long)statistics.used_heap_size(),
                         "total", (long long)statistics.total_heap_size(),
                         "limit", (long long)statistics.heap_size_limit());
}

PyGetSetDef context_getset[] = {
    {(char*)"isolate_id", (getter)PythonBoundary<context_isolate_id>::call, NULL, NULL, NULL},
    {(char*)"live_handles", (getter)PythonBoundary<context_live_handles>::call, NULL, NULL, NULL},
    {(char*)"heap_terminations", (getter)PythonBoundary<context_heap_terminations>::call, NULL, NULL, NULL},
    {(char*)"glob", (getter)PythonBoundary<context_get_global>::call, NULL, NULL, NULL},
    {(char*)"detached", (getter)PythonBoundary<context_get_detached>::call, NULL, NULL, NULL},
    {(char*)"trace", (getter)PythonBoundary<context_get_trace>::call, (setter)PythonBoundary<context_set_trace>::call, NULL, NULL},
    {(char*)"timeout", (getter)PythonBoundary<context_get_timeout>::call, (setter)PythonBoundary<context_set_timeout>::call, NULL, NULL},
    {NULL},
};
// We don't need mapping methods for ABI3 compatibility

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot context_slots[] = {
    {Py_tp_traverse, (void*)PythonBoundary<context_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<context_clear>::call},
    {Py_tp_dealloc, (void*)PythonBoundary<context_dealloc>::call},
    {Py_tp_new, (void*)PythonBoundary<context_new>::call},
    {Py_tp_methods, context_methods},
    {Py_tp_getattro, (void*)PythonBoundary<context_getattro>::call},
    {Py_tp_setattro, (void*)PythonBoundary<context_setattro>::call},
    {Py_tp_getset, context_getset},
    {Py_tp_doc, (void*)"v8 context"},
    // We can't use Py_tp_as_mapping in Limited API
    // {Py_tp_as_mapping, &context_mapping_for_spec},
    {0, NULL} };

static PyType_Spec context_spec = {
    "pyv8.Context",
    sizeof(context_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC,
    context_slots };

PyTypeObject* context_type = NULL;

int context_type_init()
{
    // Create the type using PyType_FromSpec for ABI3 compatibility
    context_type = (PyTypeObject *) PyType_FromSpec(&context_spec);
    if (context_type == NULL)
    {
        return -1;
    }
    // We don't want to DECREF here because we need to keep the type object alive
    return 0;
}


PyObject *
context_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    if (!runtime_manager.ensure_initialized())
        return NULL;

    double timeout = 0;
    PyObject* py_timeout = Py_None;
    PyObject* global = NULL;
    PyObject* trace = Py_False;
    PyObject* ctx_type = NULL;
    PyObject* py_isolate = Py_None;
    PyObject* lazy_factory = Py_None;
    PyObject* trace_callback = Py_None;
    PyObject* traceable = Py_False;
    PyObject* reuse_global = Py_None;
    static const char *keywords[] = {"global_this", "trace", "ctx_type", "timeout", "isolate", "lazy_factory", "trace_callback", "traceable", "reuse_global", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOOOOOOO", (char **) keywords,
                                    &global, &trace, &ctx_type, &py_timeout, &py_isolate,
                                    &lazy_factory, &trace_callback, &traceable,
                                    &reuse_global)) {
        return NULL;
    }

    if (py_timeout != Py_None) timeout = PyFloat_AsDouble(py_timeout);
    if (PyErr_Occurred() || !valid_timeout(timeout)) return nullptr;

    if (lazy_factory != Py_None && !PyCallable_Check(lazy_factory)) {
        PyErr_SetString(PyExc_TypeError, "lazy_factory must be callable or None");
        return NULL;
    }

    if (trace_callback != Py_None && !PyCallable_Check(trace_callback)) {
        PyErr_SetString(PyExc_TypeError, "trace_callback must be callable or None");
        return nullptr;
    }
    IsolateState* isolate_state = isolate_state_from_python(py_isolate, true);
    if (isolate_state == nullptr)
        return NULL;
    IN_ISOLATE(isolate_state);

    // The frame's window again, which V8 accepts from the same template.
    Local<Value> reused;
    if (reuse_global != Py_None) {
        if (!PyObject_TypeCheck(reuse_global, js_object_type)) {
            PyErr_SetString(PyExc_TypeError, "reuse_global expects a realm's global object");
            return NULL;
        }
        js_object* reused_handle = (js_object*)reuse_global;
        if (reused_handle->isolate_state != isolate_state) {
            PyErr_SetString(PyExc_RuntimeError, "reuse_global belongs to another isolate");
            return NULL;
        }
        if (!js_object_require_active(reused_handle))
            return NULL;
        Local<Object> reused_object = reused_handle->object.Get(isolate);
        if (reused_object.IsEmpty() ||
            !isolate_state->transfer_python_object(reused_object)) {
            return NULL;
        }
        reused = reused_object;
    }

    if (global != NULL) {
        if (PyType_Check(global)) {
            PyObject* no_args = PyTuple_New(0);
            PyErr_PROPAGATE(no_args);
            global = PyObject_Call(global, no_args, NULL);
            Py_XDECREF((PyObject*)no_args);
            PyErr_PROPAGATE(global);
        }
        else {
            Py_INCREF((PyObject*)global);
        }
    }

    context_c* self = (context_c*)PyType_GenericAlloc(type, 0);
    if (self == NULL) {
        Py_XDECREF((PyObject*)global);
        return NULL;
    }
    self->isolate_state = isolate_state;
    self->py_isolate = nullptr;
    if (py_isolate != Py_None) {
        Py_INCREF((PyObject*)py_isolate);
        self->py_isolate = py_isolate;
    }
    self->lazy_factory = NULL;
    self->py_global = NULL;
    self->registry = PyDict_New();
    self->function_template_cache = PyDict_New();
    if (lazy_factory != Py_None) {
        Py_INCREF((PyObject*)lazy_factory);
        self->lazy_factory = lazy_factory;
    }
    self->py_global = global;
    self->has_done = false;
    self->detached = false;
    self->timeout = timeout;
    self->trace = trace == Py_True;
    self->traceable = self->trace || traceable == Py_True;
    self->trace_depth = 0;
    if (trace_callback != Py_None) {
        Py_INCREF((PyObject*)trace_callback);
        self->trace_callback = trace_callback;
    } else if (self->traceable) {
        PyObject* module = PyImport_ImportModule("pyv8.trace");
        if (module != nullptr) {
            self->trace_callback = PyObject_GetAttrString(module, "log_trace");
            Py_DECREF((PyObject*)module);
        }
        if (self->trace_callback == nullptr) {
            Py_DECREF((PyObject*)self);
            return nullptr;
        }
    }

    if (ctx_type == NULL) {
        ctx_type = PyUnicode_FromString("Top");
    } else {
        Py_INCREF((PyObject*)ctx_type);
    }
    if (ctx_type == NULL || self->registry == NULL || self->function_template_cache == NULL) {
        Py_XDECREF((PyObject*)ctx_type);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    
    if (self->trace_callback != nullptr) {
        // Event names indexed by the kind passed to TraceEvent::emit: 0 get,
        // 1 set, 2 method, 3 construct, 4 has, 5 descriptor.
        self->trace_event_names = Py_BuildValue(
            "(ssssss)", "get", "set", "method", "construct", "has", "descriptor");
        self->trace_context_type = ctx_type;
        Py_INCREF((PyObject*)ctx_type);
        if (self->trace_event_names == nullptr) {
            Py_DECREF((PyObject*)ctx_type);
            Py_DECREF((PyObject*)self);
            return nullptr;
        }
    }
    MaybeLocal<ObjectTemplate> global_template;
    if (global != NULL) {

        PyObject* global_type;
        if (!PyType_Check(global)) {
        //if (PyInstance_Check(global)) {
            global_type = PyObject_GetAttrString(global, "__class__");
        }
        else {
            global_type = (PyObject*)Py_TYPE((PyObject*)global);
            Py_INCREF((PyObject*)global_type);
        }
        if (global_type == NULL) {
            Py_DECREF((PyObject*)ctx_type);
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        py_class* templ;
        templ = (py_class*)py_class_to_template(global_type, self);
        Py_XDECREF((PyObject*)global_type);
        if (templ == NULL) {
            Py_DECREF((PyObject*)ctx_type);
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        Local<FunctionTemplate> global_inteface_template = templ->templ.Get(isolate);
        global_template = global_inteface_template->InstanceTemplate();
        Py_DECREF((PyObject*)templ);

    }

    IN_CONTEXT(Context::New(isolate, NULL, global_template, reused));
    self->js_context.Set(isolate_state, isolate, context);
    self->has_done = true;

    // add env to vm
    //IN_CONTEXT(self->js_context.Get(isolate));
    context->SetEmbedderData(CTX_TYPE, V8String(isolate, PyUnicode_AsUTF8_abi3(ctx_type)));
    Py_DECREF((PyObject*)ctx_type);
    if (!self->resources) self->resources = new ContextResources();
    if (!self->resources->lifetime) self->resources->lifetime = std::make_shared<ContextLifetime>(self);
    context->SetEmbedderData(CONTEXT_OBJECT_SLOT, v8_compat::external_new(isolate, self->resources->lifetime.get()));
    context->SetEmbedderData(OBJECT_PROTOTYPE_SLOT, Object::New(isolate)->GetPrototype());

    if (global != NULL) {
        if (!py_class_init_js_object(
                v8_compat::global_template_object(context), global, context)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
    return (PyObject *) self;
}

// Every Python reference this object owns, walked by both traverse and clear so
// the two lists cannot disagree.
static void context_clear_python_fields(context_c* self)
{
#define PYV8_CLEAR_FIELD(name) Py_CLEAR(self->name);
    CONTEXT_PY_FIELDS(PYV8_CLEAR_FIELD)
#undef PYV8_CLEAR_FIELD
}

void
context_finish_close(context_c *self)
{
    self->state = ContextState::Closing;
    if (self->isolate_state && self->isolate_state->is_active())
        self->isolate_state->watchdog().release_owner(self);
    debugger_context_closed(self);
    if (self->resources) self->resources->unlink_dependencies(self);
    else Py_CLEAR(self->dependencies);
    if (self->isolate_state != nullptr) {
        self->isolate_state->invalidate_callbacks(self);
        if (!self->js_context.IsEmpty())
            self->js_context.Get(self->isolate_state->isolate())->SetEmbedderData(
                CONTEXT_OBJECT_SLOT, v8_compat::external_new(self->isolate_state->isolate(), nullptr));
        self->isolate_state->release_context_owners(self);
    }
    if (self->resources != nullptr) {
        while (!self->resources->handles.empty()) {
            auto item = *self->resources->handles.begin();
            self->resources->handles.erase(item.first);
            Py_INCREF(item.first);
            item.second(item.first);
            Py_DECREF(item.first);
        }
        delete self->resources;
        self->resources = nullptr;
    }
    // The V8 side goes first: the references below are Python's, and dropping
    // them while the realm they came from is still standing is exactly what
    // closing is meant to avoid.
    self->js_context.Reset();
    context_clear_python_fields(self);
    self->isolate_state = nullptr;
    self->state = ContextState::Closed;
}

static int context_traverse(context_c* self, visitproc visit, void* arg) {
#define PYV8_VISIT_FIELD(name) Py_VISIT(self->name);
    CONTEXT_PY_FIELDS(PYV8_VISIT_FIELD)
#undef PYV8_VISIT_FIELD
    if (self->isolate_state != nullptr)
        return self->isolate_state->traverse_context_owners(self, visit, arg);
    return 0;
}

static int context_clear(context_c* self) {
    if (self->state != ContextState::Active) return 0;
    auto* state = self->isolate_state;
    if (state == nullptr) { context_finish_close(self); return 0; }
    IN_ISOLATE(state);
    self->state = ContextState::Closing;
    state->watchdog().release_owner(self);
    // Callback frames still borrow binding metadata until the outer entry exits.
    if (!state->defer_context_close(self))
        context_finish_close(self);
    return 0;
}

PyObject*
context_close(context_c *self, PyObject* ignored)
{
    (void)ignored;
    if (!debugger_context_can_close(self)) return nullptr;
    context_clear(self);
    PYV8_RETURN_NONE;
}

void
context_dealloc(context_c *self)
{
    PyObject_GC_UnTrack(self);
    context_clear(self);
    if (Py_REFCNT((PyObject*)self) != 0) return;
    TP_FREE_ABI3(context_type, self);
}


PyObject*
context_js_obj(context_c* self, PyObject* py_object)
{
    // A pair that already has a handle is answered from the table alone; only
    // building the first one enters the isolate.
    if (!context_is_active(self)) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is closed");
        return nullptr;
    }

    PyObject* wrapper = self->isolate_state->cached_js_wrapper(self, py_object);
    if (wrapper == nullptr) {
        if (PyErr_Occurred()) return nullptr;
        PYV8_RETURN_NONE;
    }
    return wrapper;
}

PyObject *
context_run(context_c *self, PyObject *args, PyObject *kwargs)
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    auto* isolate_state = self->isolate_state;
    PyObject *program;
    PyObject *filename = Py_None;
    double timeout = self->timeout;
    PyObject* py_timeout = Py_None;
    static const char *keywords[] = {"program", "filename", "timeout", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO", (char **) keywords, &program, &filename, &py_timeout)) {
        return NULL;
    }
    if (py_timeout != Py_None) timeout = PyFloat_AsDouble(py_timeout);
    if (PyErr_Occurred() || !valid_timeout(timeout)) return nullptr;
    if (!PyUnicode_Check(program) && !PyObject_IsInstance(program, (PyObject *)script_type))
    {
        PyErr_SetString(PyExc_TypeError, "program must be a string or Script");
        return NULL;
    }
    if (PyUnicode_Check(program))
    {
        program = PyObject_CallFunctionObjArgs((PyObject *)script_type, (PyObject*)self, program, filename, NULL);
        PyErr_PROPAGATE(program);
    }
    else
    {
        Py_INCREF((PyObject*)program);
    }
    assert(PyObject_IsInstance(program, (PyObject*)script_type));
    script_c *py_script = (script_c *) program;

    if (py_script->script.IsEmpty()) {
        Py_DECREF((PyObject*)program);
        PyErr_SetString(PyExc_RuntimeError, "V8 script context is closed");
        return nullptr;
    }
    if (py_script->isolate_state != isolate_state) {
        Py_DECREF((PyObject*)program);
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return NULL;
    }

    IN_CONTEXT(self->js_context.Get(isolate));
    JS_TRY

    Local<UnboundScript> unbound_script = py_script->script.Get(isolate);
    Py_XDECREF((PyObject*)program);
    Local<Script> script = unbound_script->BindToCurrentContext();

    DeadlineScope timeout_guard(timeout, isolate);
    timeout_guard.start();
    debugger_prepare_script(self);
    MaybeLocal<Value> result = without_gil([&] { return script->Run(context); });

    timeout_guard.stop();
    PY_PROPAGATE_JS;
    Local<Value> js_result;
    if (!result.ToLocal(&js_result)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to execute JavaScript");
        return NULL;
    }
    return py_from_js(js_result, context);
}


PyObject*
context_get_trace(context_c* self, void*)
{
    return PyBool_FromLong(self->trace);
}

int
context_set_trace(context_c* self, PyObject* value, void*)
{
    if (value == nullptr || !PyBool_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "trace must be a bool");
        return -1;
    }
    ExecutionScope execution(self, value);
    if (!execution) {
        // Keep this property's established closed-context error while sharing
        // the guarded entry and its post-lock lifecycle validation.
        if (!context_is_active(self)) context_require_isolate(self);
        return -1;
    }
    if (value == Py_True && !self->traceable) {
        PyErr_SetString(PyExc_RuntimeError, "create Context with trace=True or traceable=True before enabling trace");
        return -1;
    }
    self->trace = value == Py_True;
    return 0;
}

PyObject *
context_get_timeout(context_c *self, void *ignored)
{
    return PyFloat_FromDouble(self->timeout);
}

int
context_set_timeout(context_c *self, PyObject *value, void *ignored)
{
    double timeout = PyFloat_AsDouble(value);
    if (PyErr_Occurred())
        return -1;
    if (!valid_timeout(timeout)) return -1;
    self->timeout = timeout;
    return 0;
}

PyObject *
context_get_global(context_c *self, void *ignored)
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    PyObject* pyobj = py_from_js(
        v8_compat::global_template_object(context), context);
    PyErr_PROPAGATE(pyobj);
    PyObject* result = context_js_obj(self, pyobj);
    Py_DECREF((PyObject*)pyobj);
    return result;
}

PyObject *
context_getattro(context_c *self, PyObject *name)
{
    PyObject *value = PyObject_GenericGetAttr((PyObject *) self, name);
    if (value == NULL) {
        PyErr_Clear();
        return context_getitem(self, name);
    }
    return value;
}

PyObject *
context_getitem(context_c *self, PyObject *name)
{
    PyObject* global = context_get_global(self, NULL);
    PyErr_PROPAGATE(global);
    PyObject* result = PyObject_GetAttr(global, name);
    Py_DECREF((PyObject*)global);
    return result;
}

int 
context_setattro(context_c *self, PyObject *name, PyObject *value)
{
    // use GenericGetAttr to find out if Context defines the property
    PyObject *ctx_value = PyObject_GenericGetAttr((PyObject *) self, name);
    if (ctx_value == NULL) {
        // if the property is not defined by Context, set it on the global
        PyErr_Clear();
        return context_setitem(self, name, value);
    } else {
        Py_XDECREF((PyObject*)ctx_value);
    }
    // if the property is defined by Context, delegate
    return PyObject_GenericSetAttr((PyObject *) self, name, value);
}

int 
context_setitem(context_c *self, PyObject *name, PyObject *value)
{
    PyObject *global = context_get_global(self, NULL);
    PyErr_PROPAGATE_(global);
    int result = PyObject_SetAttr(global, name, value);
    Py_DECREF((PyObject*)global);
    return result;
}

PyObject *
context_gc(context_c *self) 
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    isolate->LowMemoryNotification();
    PYV8_RETURN_NONE;
}

PyObject *
context_enqueue_microtask(context_c *self, PyObject *function)
{
#ifndef V8_CPPGC_MICROTASK_QUEUE
    // The macro decides MicrotaskQueue's layout and only the library's own command
    // line defines it: built without it, every virtual call would reach the wrong
    // slot.
    PyErr_SetString(PyExc_RuntimeError,
                    "this V8 build does not use cppgc microtask queues");
    return NULL;
#else
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    IN_CONTEXT(self->js_context.Get(isolate));
    Local<Value> microtask = js_from_py(function, context);
    if (microtask.IsEmpty() || PyErr_Occurred())
        return NULL;
    if (!microtask->IsFunction()) {
        PyErr_SetString(PyExc_TypeError, "microtask must be a function");
        return NULL;
    }
    MicrotaskQueue* queue = context->GetMicrotaskQueue();
    if (queue == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context has no microtask queue");
        return NULL;
    }
    queue->EnqueueMicrotask(isolate, microtask.As<Function>());
    PYV8_RETURN_NONE;
#endif
}

PyObject *
context_set_error_hooks(context_c *self, PyObject *args, PyObject *kwargs)
{
    // Each call replaces both reporters for this Context only.
    PyObject *on_uncaught = Py_None;
    PyObject *on_rejection = Py_None;
    static const char *keywords[] = {"on_uncaught", "on_rejection", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO", (char **) keywords,
                                     &on_uncaught, &on_rejection)) {
        return NULL;
    }
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    if (!error_hooks_install(self, on_uncaught, on_rejection))
        return NULL;
    PYV8_RETURN_NONE;
}

PyObject*
context_to_js(context_c* self, PyObject* value)
{
    // Converts a Python value into this realm: the structural counterpart of
    // `pyv8.transfer`, and the reason a transfer needs no JavaScript trampoline.
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    Local<Context> base = execution.context();
    Local<Value> converted = js_from_py(value, base);
    if (converted.IsEmpty() || PyErr_Occurred())
        return nullptr;
    return py_from_js(converted, base);
}

PyObject*
context_import_handle(context_c* self, PyObject* handle)
{
    // A handle from another realm of the same isolate: taking a reference on its
    // realm here is what keeps that realm alive until this one closes.
    ExecutionScope execution(self, handle);
    if (!execution) return nullptr;
    auto* state = self->isolate_state;
    PyObject* source = nullptr;
    if (PyObject_TypeCheck(handle, js_object_type))
    {
        js_object* value = (js_object*)handle;
        if (value->object.IsEmpty() || value->isolate_state == nullptr ||
            (value->registration.context && !context_is_active((context_c*)value->registration.context))) {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value Context is closed");
            return nullptr;
        }
        if (!js_object_require_active(value))
            return nullptr;
        if (value->isolate_state != state)
        {
            PyErr_SetString(PyExc_RuntimeError,
                            "JavaScript value belongs to another isolate; "
                            "use pyv8.transfer() to copy it");
            return nullptr;
        }
        source = value->py_context;
    }
    else if (PyObject_TypeCheck(handle, js_value_type))
    {
        js_value* value = (js_value*)handle;
        if (value->value.IsEmpty() || value->isolate_state == nullptr ||
            (value->registration.context && !context_is_active(value->registration.context))) {
            PyErr_SetString(PyExc_RuntimeError, "JavaScript value Context is closed");
            return nullptr;
        }
        if (value->isolate_state != state)
        {
            PyErr_SetString(PyExc_RuntimeError,
                            "JavaScript value belongs to another isolate; "
                            "use pyv8.transfer() to copy it");
            return nullptr;
        }
        source = value->py_context;
    }
    else
    {
        PyErr_SetString(PyExc_TypeError, "import_handle expects a JSObject or JSValue");
        return nullptr;
    }
    if (source == nullptr || source == (PyObject*)self)
    {
        Py_INCREF(handle);
        return handle;
    }
    if (!context_retain_dependency(execution.context(), source))
        return nullptr;
    Py_INCREF(handle);
    return handle;
}

PyObject*
context_set_deadline(context_c* self, PyObject* args, PyObject* kwargs)
{
    PyObject* seconds = NULL;
    static const char* keywords[] = {"seconds", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", (char**)keywords, &seconds))
        return NULL;
    double value = PyFloat_AsDouble(seconds);
    if (PyErr_Occurred() || !valid_timeout(value))
        return NULL;
    auto* state = context_require_isolate(self);
    if (state == nullptr)
        return NULL;
    state->watchdog().arm(value, self);
    PYV8_RETURN_NONE;
}

PyObject*
context_clear_deadline(context_c* self, PyObject* ignored)
{
    (void)ignored;
    // A command clears the deadline of the context it borrowed, and that command may
    // have closed the context while it ran.  The arm being undone belongs to the
    // isolate's watchdog, which is still there, so it is disarmed through the isolate
    // rather than through the context: leaving it armed would fire on whatever runs
    // next, long after the command that asked for it finished.
    IsolateState* state = self->isolate_state;
    if (state != nullptr && state->is_active())
        state->watchdog().disarm(self);
    PYV8_RETURN_NONE;
}

PyObject*
context_terminate(context_c* self, PyObject* ignored)
{
    (void)ignored;
    auto* state = context_require_isolate(self);
    if (state == nullptr)
        return NULL;
    state->watchdog().terminate_now();
    PYV8_RETURN_NONE;
}
