#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <algorithm>

#include "context.h"
#include "debugger.h"
#include "error_hooks.h"
#include "idl_spec.h"
#include "script.h"
#include "convert.h"
#include "jsobject.h"
#include "pyclass.h"
#include "v8tools.h"
#include "isolate.h"
#include "timeout_guard.h"

using namespace v8;

static PyObject* context_try_inspector(context_c* self, PyObject* args);
static int context_clear(context_c* self);
static int context_traverse(context_c* self, visitproc visit, void* arg);

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
    } catch (const std::bad_alloc&) {
        if (origin->resources) origin->resources->dependents.erase(owner);
        if (owner->resources) owner->resources->dependencies.erase(origin);
        PyErr_NoMemory();
        return false;
    }
    if (PyList_Append(owner->dependencies, source) < 0) {
        origin->resources->dependents.erase(owner);
        owner->resources->dependencies.erase(origin);
        return false;
    }
    return true;
}

static void context_unlink_dependencies(context_c* self) {
    if (self->resources != nullptr) {
        auto& dependents = self->resources->dependents;
        while (!dependents.empty()) {
            auto* target = *dependents.begin();
            dependents.erase(target);
            if (target->resources != nullptr) target->resources->dependencies.erase(self);
            if (target->dependencies == nullptr) continue;
            for (Py_ssize_t i = PyList_Size(target->dependencies); i-- > 0;)
                if (PyList_GetItem(target->dependencies, i) == (PyObject*)self)
                    PySequence_DelItem(target->dependencies, i);
        }
        for (auto* origin : self->resources->dependencies) {
            if (origin->resources != nullptr) origin->resources->dependents.erase(self);
        }
        self->resources->dependencies.clear();
    }
    Py_CLEAR(self->dependencies);
}

PyMethodDef context_methods[] = {
    {"_try_inspector", (PyCFunction)PythonBoundary<context_try_inspector>::call, METH_VARARGS, NULL},
    {"exec_js", (PyCFunction)PythonBoundary<context_run>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"js_obj", (PyCFunction)PythonBoundary<context_js_obj>::call, METH_O, NULL},
    {"expose", (PyCFunction)PythonBoundary<context_expose>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"expose_module", (PyCFunction)PythonBoundary<context_expose_module>::call, METH_O, NULL},
    {"declare_lazy", (PyCFunction)PythonBoundary<context_declare_lazy>::call, METH_VARARGS | METH_KEYWORDS, NULL},
    {"update_configs", (PyCFunction)PythonBoundary<context_update_configs>::call, METH_O, NULL},
    {"gc", (PyCFunction)PythonBoundary<context_gc>::call, METH_NOARGS, NULL},
    {"enqueue_microtask", (PyCFunction)PythonBoundary<context_enqueue_microtask>::call, METH_O, NULL},
    {"set_error_hooks", (PyCFunction)PythonBoundary<context_set_error_hooks>::call,
     METH_VARARGS | METH_KEYWORDS, NULL},
    {"close", (PyCFunction)PythonBoundary<context_close>::call, METH_NOARGS, NULL},
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
        auto& waiters = state->inspector_waiters;
        if (std::find(waiters.begin(), waiters.end(), wake) == waiters.end()) {
            Py_INCREF((PyObject*)wake);
            waiters.push_back(wake);
        }
        Py_RETURN_FALSE;
    }
    PyObject* result = PyObject_CallNoArgs(callback);
    if (!result) return nullptr;
    Py_DECREF((PyObject*)result);
    Py_RETURN_TRUE;
}
// Python is wrong. The first entry is not modifiable and should be const char *
static PyObject* context_isolate_id(context_c* self, void*) {
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    return PyLong_FromVoidPtr(state->isolate());
}

PyGetSetDef context_getset[] = {
    {(char*)"isolate_id", (getter)PythonBoundary<context_isolate_id>::call, NULL, NULL, NULL},
    {(char*)"glob", (getter)PythonBoundary<context_get_global>::call, NULL, NULL, NULL},
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
    "_pyv8env.Context",
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
    if (!runtime_manager.require_initialized())
        return NULL;

    double timeout = 0;
    PyObject* global = NULL;
    PyObject* trace = Py_False;
    PyObject* ctx_type = NULL;
    PyObject* py_isolate = Py_None;
    PyObject* class_configs = Py_None;
    PyObject* lazy_factory = Py_None;
    PyObject* trace_callback = Py_None;
    PyObject* traceable = Py_False;
    static const char *keywords[] = {"global_this", "trace", "ctx_type", "timeout", "isolate", "class_configs", "lazy_factory", "trace_callback", "traceable", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOdOOOOO", (char **) keywords,
                                    &global, &trace, &ctx_type, &timeout, &py_isolate,
                                    &class_configs, &lazy_factory, &trace_callback, &traceable)) {
        return NULL;
    }

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
    self->class_configs = NULL;
    self->lazy_factory = NULL;
    self->resolving_classes = PySet_New(nullptr);
    self->lazy_names = PySet_New(nullptr);
    self->py_global = NULL;
    self->class_template_cache = PyDict_New();
    self->function_template_cache = PyDict_New();
    if (class_configs != Py_None && PyDict_Check(class_configs)) {
        // Keep the caller's mapping alive so Python can add realm-specific
        // class overlays when a lazy interface is first accessed.
        self->class_configs = PyDict_Copy(class_configs);
    }
    else {
        self->class_configs = PyDict_New();
    }
    if (self->class_configs != NULL && class_configs != Py_None && !PyDict_Check(class_configs)) {
        if (!PyMapping_Check(class_configs)) {
            PyErr_SetString(PyExc_TypeError, "class_configs must be a mapping or None");
            Py_DECREF((PyObject*)self);
            Py_XDECREF((PyObject*)global);
            return NULL;
        }
        if (PyDict_Update(self->class_configs, class_configs) < 0) {
            Py_DECREF((PyObject*)self);
            Py_XDECREF((PyObject*)global);
            return NULL;
        }
    }
    if (lazy_factory != Py_None) {
        Py_INCREF((PyObject*)lazy_factory);
        self->lazy_factory = lazy_factory;
    }
    self->py_global = global;
    self->has_done = false;
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
    if (self->class_configs != NULL && !idl_spec_validate_configs(self->class_configs)) {
        Py_XDECREF((PyObject*)ctx_type);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    if (ctx_type == NULL || self->class_template_cache == NULL || self->function_template_cache == NULL || self->class_configs == NULL || self->resolving_classes == nullptr || self->lazy_names == nullptr) {
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

    }

    IN_CONTEXT(Context::New(isolate, NULL, global_template));
    self->js_context.Reset(isolate, context);

    isolate_state->retain_v8_handle();
    self->has_done = true;

    // add env to vm
    //IN_CONTEXT(self->js_context.Get(isolate));
    context->SetEmbedderData(CTX_TYPE, V8String(isolate, PyUnicode_AsUTF8_abi3(ctx_type)));
    Py_DECREF((PyObject*)ctx_type);
    if (!self->resources) self->resources = new ContextResources();
    if (!self->resources->lifetime) self->resources->lifetime = std::make_shared<ContextLifetime>(self);
    context->SetEmbedderData(CONTEXT_OBJECT_SLOT, v8_compat::external_new(isolate, self->resources->lifetime.get()));
    context->SetEmbedderData(OBJECT_PROTOTYPE_SLOT, Object::New(isolate)->GetPrototype());
    context->SetEmbedderData(ERROR_PROTOTYPE_SLOT, Exception::Error(String::Empty(isolate)).As<Object>()->GetPrototype());

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
static const std::size_t context_gc_fields[] = {
    offsetof(context_c, py_isolate),
    offsetof(context_c, class_template_cache),
    offsetof(context_c, function_template_cache),
    offsetof(context_c, callback_targets),
    offsetof(context_c, class_configs),
    offsetof(context_c, lazy_factory),
    offsetof(context_c, resolving_classes),
    offsetof(context_c, lazy_names),
    offsetof(context_c, py_global),
    offsetof(context_c, trace_callback),
    offsetof(context_c, trace_context_type),
    offsetof(context_c, trace_event_names),
    offsetof(context_c, uncaught_handler),
    offsetof(context_c, rejection_handler),
    offsetof(context_c, dependencies),
};

// The place a PyObject* lives in this object, for a given entry of the list.
static PyObject** context_gc_slot(context_c* self, std::size_t offset)
{
    return reinterpret_cast<PyObject**>(reinterpret_cast<char*>(self) + offset);
}

void
context_finish_close(context_c *self)
{
    self->state = ContextState::Closing;
    debugger_context_closed(self);
    context_unlink_dependencies(self);
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
    if (!self->js_context.IsEmpty()) {
        self->js_context.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    for (std::size_t offset : context_gc_fields)
        Py_CLEAR(*context_gc_slot(self, offset));
    self->isolate_state = nullptr;
    self->state = ContextState::Closed;
}

static int context_traverse(context_c* self, visitproc visit, void* arg) {
    for (std::size_t offset : context_gc_fields)
        Py_VISIT(*context_gc_slot(self, offset));
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
    // Callback frames still borrow binding metadata until the outer entry exits.
    if (state->entry_depth > 1) {
        Py_INCREF((PyObject*)self);
        self->next_pending_close = state->pending_context_closes;
        state->pending_context_closes = self;
    } else {
        context_finish_close(self);
    }
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
    static const char *keywords[] = {"program", "filename", "timeout", NULL};
    // python needs to fix their shit and make it const
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "O|Od", (char **) keywords, &program, &filename, &timeout) < 0) {
        return NULL;
    }
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

    TimeoutGuard timeout_guard(timeout, isolate);
    if (!timeout_guard.start()) return NULL;
    debugger_prepare_script(self);
    MaybeLocal<Value> result = script->Run(context);

    if (!timeout_guard.stop()) return NULL;
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
    auto* state = context_require_isolate(self);
    if (state == nullptr) return -1;
    IN_ISOLATE(state);
    if (context_require_isolate(self) == nullptr) return -1;
    if (value == Py_True && !self->traceable) {
        PyErr_SetString(PyExc_RuntimeError, "create Context with trace=True or traceable=True before enabling trace");
        return -1;
    }
    self->trace = value == Py_True;
    return 0;
}

PyObject *
context_get_timeout(context_c *self, void *shit)
{
    return PyFloat_FromDouble(self->timeout);
}

int
context_set_timeout(context_c *self, PyObject *value, void *shit)
{
    double timeout = PyFloat_AsDouble(value);
    if (PyErr_Occurred())
        return -1;
    self->timeout = timeout;
    return 0;
}

PyObject *
context_get_global(context_c *self, void *shit)
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
