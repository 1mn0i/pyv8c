#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <algorithm>

#include "context.h"
#include "debugger.h"
#include "idl_spec.h"
#include "script.h"
#include "convert.h"
#include "jsobject.h"
#include "pyclass.h"
#include "v8tools.h"
#include "pyfunction.h"
#include "jsfunction.h"
#include "isolate.h"
#include "timeout_guard.h"
#include "faker/trace.h"

using namespace v8;

static PyObject* context_try_inspector(context_c* self, PyObject* args);

PyMethodDef context_methods[] = {
    {"_try_inspector", (PyCFunction)context_try_inspector, METH_VARARGS, NULL},
    {"exec_js", (PyCFunction)context_run, METH_VARARGS | METH_KEYWORDS, NULL},
    {"js_obj", (PyCFunction)context_js_obj, METH_O, NULL},
    {"expose", (PyCFunction)context_expose, METH_VARARGS | METH_KEYWORDS, NULL},
    {"expose_module", (PyCFunction)context_expose_module, METH_O, NULL},
    {"declare_lazy", (PyCFunction)context_declare_lazy, METH_VARARGS | METH_KEYWORDS, NULL},
    {"update_configs", (PyCFunction)context_update_configs, METH_O, NULL},
    {"gc", (PyCFunction)context_gc, METH_NOARGS, NULL},
    {"close", (PyCFunction)context_close, METH_NOARGS, NULL},
    {NULL},
};

static IsolateState*
context_require_isolate(context_c* self)
{
    if (self->isolate_state == nullptr || !self->isolate_state->is_active() || self->js_context.IsEmpty())
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
            Py_INCREF(wake);
            waiters.push_back(wake);
        }
        Py_RETURN_FALSE;
    }
    PyObject* result = PyObject_CallNoArgs(callback);
    if (!result) return nullptr;
    Py_DECREF(result);
    Py_RETURN_TRUE;
}
// Python is wrong. The first entry is not modifiable and should be const char *
static PyObject* context_isolate_id(context_c* self, void*) {
    auto state = context_require_isolate(self);
    if (!state) return nullptr;
    return PyLong_FromVoidPtr(state->isolate());
}

PyGetSetDef context_getset[] = {
    {(char*)"isolate_id", (getter)context_isolate_id, NULL, NULL, NULL},
    {(char*)"glob", (getter)context_get_global, NULL, NULL, NULL},
    {(char*)"trace", (getter)context_get_trace, (setter)context_set_trace, NULL, NULL},
    {(char*)"timeout", (getter)context_get_timeout, (setter)context_set_timeout, NULL, NULL},
    {NULL},
};
// We don't need mapping methods for ABI3 compatibility

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot context_slots[] = {
    {Py_tp_dealloc, (void*)context_dealloc},
    {Py_tp_new, (void*)context_new},
    {Py_tp_methods, context_methods},
    {Py_tp_getattro, (void*)context_getattro},
    {Py_tp_setattro, (void*)context_setattro},
    {Py_tp_getset, context_getset},
    {Py_tp_doc, (void*)"v8 context"},
    // We can't use Py_tp_as_mapping in Limited API
    // {Py_tp_as_mapping, &context_mapping_for_spec},
    {0, NULL} };

static PyType_Spec context_spec = {
    "_pyv8env.Context",
    sizeof(context_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
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
    int preserve_arguments = 0;
    static const char *keywords[] = {"global_this", "trace", "ctx_type", "timeout", "isolate", "class_configs", "lazy_factory", "trace_callback", "traceable", "preserve_arguments", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOdOOOOOp", (char **) keywords,
                                    &global, &trace, &ctx_type, &timeout, &py_isolate,
                                    &class_configs, &lazy_factory, &trace_callback, &traceable, &preserve_arguments)) {
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
            Py_XDECREF(no_args);
            PyErr_PROPAGATE(global);
        }
        else {
            Py_INCREF(global);
        }
    }

    context_c* self = (context_c*)PyType_GenericAlloc(type, 0);
    if (self == NULL) {
        Py_XDECREF(global);
        return NULL;
    }
    self->isolate_state = isolate_state;
    self->py_isolate = nullptr;
    if (py_isolate != Py_None) {
        Py_INCREF(py_isolate);
        self->py_isolate = py_isolate;
    }
    self->js_object_cache = NULL;
    self->class_configs = NULL;
    self->lazy_factory = NULL;
    self->resolving_classes = PySet_New(nullptr);
    self->lazy_names = PySet_New(nullptr);
    self->py_global = NULL;
    self->class_template_cache = PyDict_New();
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
            Py_XDECREF(global);
            return NULL;
        }
        if (PyDict_Update(self->class_configs, class_configs) < 0) {
            Py_DECREF((PyObject*)self);
            Py_XDECREF(global);
            return NULL;
        }
    }
    if (lazy_factory != Py_None) {
        Py_INCREF(lazy_factory);
        self->lazy_factory = lazy_factory;
    }
    self->py_global = global;
    self->has_done = false;
    self->timeout = timeout;
    self->trace = trace == Py_True;
    self->traceable = self->trace || traceable == Py_True;
    self->preserve_arguments = preserve_arguments != 0;
    self->trace_depth = 0;
    if (trace_callback != Py_None) {
        Py_INCREF(trace_callback);
        self->trace_callback = trace_callback;
    } else if (self->traceable) {
        PyObject* module = PyImport_ImportModule("pyv8.trace");
        if (module != nullptr) {
            self->trace_callback = PyObject_GetAttrString(module, "log_trace");
            Py_DECREF(module);
        }
        if (self->trace_callback == nullptr) {
            Py_DECREF((PyObject*)self);
            return nullptr;
        }
    }

    if (ctx_type == NULL) {
        ctx_type = PyUnicode_FromString("Top");
    } else {
        Py_INCREF(ctx_type);
    }
    if (self->class_configs != NULL && !idl_spec_validate_configs(self->class_configs)) {
        Py_XDECREF(ctx_type);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    if (ctx_type == NULL || self->class_template_cache == NULL || self->class_configs == NULL || self->resolving_classes == nullptr || self->lazy_names == nullptr) {
        Py_XDECREF(ctx_type);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    
    if (self->trace_callback != nullptr) {
        self->trace_event_names = Py_BuildValue("(ssss)", "get", "set", "method", "construct");
        self->trace_context_type = ctx_type;
        Py_INCREF(ctx_type);
        if (self->trace_event_names == nullptr) {
            Py_DECREF(ctx_type);
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
            global_type = (PyObject*)Py_TYPE(global);
            Py_INCREF(global_type);
        }
        if (global_type == NULL) {
            Py_DECREF(ctx_type);
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        py_class* templ;
        templ = (py_class*)py_class_to_template(global_type, self);
        Py_XDECREF(global_type);
        if (templ == NULL) {
            Py_DECREF(ctx_type);
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
    Py_DECREF(ctx_type);
    context->SetEmbedderData(CONTEXT_OBJECT_SLOT, v8_compat::external_new(isolate, self));
    context->SetEmbedderData(OBJECT_PROTOTYPE_SLOT, Object::New(isolate)->GetPrototype());
    context->SetEmbedderData(ERROR_PROTOTYPE_SLOT, Exception::Error(String::Empty(isolate)).As<Object>()->GetPrototype());

    PyObject *weakref_module = PyImport_ImportModule("weakref");
    if (weakref_module == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    PyObject *weak_key_dict = PyObject_GetAttrString(weakref_module, "WeakKeyDictionary");
    Py_DECREF(weakref_module);
    if (weak_key_dict == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    self->js_object_cache = PyObject_CallObject(weak_key_dict, NULL);
    Py_DECREF(weak_key_dict);
    if (self->js_object_cache == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    if (global != NULL) {
        if (!py_class_init_js_object(
                v8_compat::global_template_object(context), global, context)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
    return (PyObject *) self;
}

static void
context_release(context_c *self)
{
    debugger_context_closed(self);
    Py_CLEAR(self->class_template_cache);
    Py_CLEAR(self->class_configs);
    Py_CLEAR(self->lazy_factory);

    Py_CLEAR(self->trace_callback);
    Py_CLEAR(self->trace_context_type);
    Py_CLEAR(self->trace_event_names);
    Py_CLEAR(self->resolving_classes);
    Py_CLEAR(self->lazy_names);
    if (!self->js_context.IsEmpty()) {
        self->js_context.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    Py_CLEAR(self->py_global);

    if (self->js_object_cache != NULL && PyObject_HasAttrString(self->js_object_cache, "items")) {
        PyObject* items = PyObject_CallMethod(self->js_object_cache, "items", nullptr);
        if (items) {
            PyObject* iterator = PyObject_GetIter(items);
            if (iterator) {
                PyObject* item;
                while ((item = PyIter_Next(iterator)) != nullptr) {
                    Py_DECREF(item);
                }
                Py_DECREF(iterator);
            }
            Py_DECREF(items);
        }
    }

    Py_CLEAR(self->js_object_cache);
    Py_CLEAR(self->py_isolate);
    self->isolate_state = nullptr;

}

PyObject*
context_close(context_c *self, PyObject* ignored)
{
    (void)ignored;
    if (!debugger_context_can_close(self)) return nullptr;
    context_release(self);
    PYV8_RETURN_NONE;
}

void
context_dealloc(context_c *self)
{
    context_release(self);
    TP_FREE_ABI3(context_type, self);
}

PyObject *
context_expose(context_c *self, PyObject *args, PyObject *kwargs)
{
    faker::TraceSilence silence(self);
    IsolateState* isolate_state = context_require_isolate(self);
    if (isolate_state == nullptr)
        return NULL;
    IN_ISOLATE(isolate_state);
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();

    args = PySequence_Fast(args, "sequence required");
    PyErr_PROPAGATE(args);
    Py_ssize_t args_size = PySequence_Size(args);
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        if (object == NULL) {
            Py_DECREF(args);
            return NULL;
        }
        if (!PyObject_HasAttrString(object, "__name__"))
        {
            PyErr_SetString(PyExc_TypeError, "Object passed to expose must have a __name__");
            Py_DECREF(object);
            Py_DECREF(args);
            return NULL;
        }
        Py_DECREF(object);
    }
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        if (object == NULL) {
            Py_DECREF(args);
            return NULL;
        }
        PyObject *name = PyObject_GetAttrString(object, "__name__");
        if (name == NULL) {
            Py_DECREF(object);
            Py_DECREF(args);
            return NULL;
        }
        Local<Value> js_name = js_from_py(name, context);
        Local<Value> js_object = js_from_py(object, context);
        if (js_name.IsEmpty() || js_object.IsEmpty() || PyErr_Occurred()) {
            Py_DECREF(object);
            Py_DECREF(name);
            Py_DECREF(args);
            return NULL;
        }
        bool created;
        if (!global->CreateDataProperty(
                context, js_name.As<String>(), js_object).To(&created)) {
            Py_DECREF(object);
            Py_DECREF(name);
            Py_DECREF(args);
            PyErr_SetString(PyExc_RuntimeError, "failed to expose Python object");
            return NULL;
        }
        Py_DECREF(object);
        Py_DECREF(name);
    }
    Py_XDECREF(args);

    if (kwargs != NULL)
    {
        PyObject *name, *object;
        Py_ssize_t pos = 0;
        while (PyDict_Next(kwargs, &pos, &name, &object))
        {
            Local<Value> js_name = js_from_py(name, context);
            Local<Value> js_object = js_from_py(object, context);
            if (js_name.IsEmpty() || js_object.IsEmpty() || PyErr_Occurred())
                return NULL;
            bool created;
            if (!global->CreateDataProperty(
                    context, js_name.As<String>(), js_object).To(&created)) {
                PyErr_SetString(PyExc_RuntimeError, "failed to expose Python object");
                return NULL;
            }
        }
    }

    PYV8_RETURN_NONE;
}

PyObject *context_update_configs(context_c *self, PyObject *mapping)
{
    if (self->class_configs == NULL || !PyDict_Check(self->class_configs))
    {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is no longer active");
        return NULL;
    }
    if (!PyDict_Check(mapping) && !PyObject_HasAttrString(mapping, "keys"))
    {
        PyErr_SetString(PyExc_TypeError, "update_configs requires a mapping of class configs");
        return NULL;
    }
    PyObject *snapshot = PyDict_New();
    if (snapshot == NULL)
        return NULL;
    if (PyDict_Update(snapshot, mapping) < 0 || !idl_spec_validate_configs(snapshot))
    {
        Py_DECREF(snapshot);
        return NULL;
    }
    Py_ssize_t added = 0;
    Py_ssize_t position = 0;
    PyObject *key = NULL;
    PyObject *value = NULL;
    while (PyDict_Next(snapshot, &position, &key, &value))
    {
        int contained = PyDict_Contains(self->class_configs, key);
        if (contained < 0)
        {
            Py_DECREF(snapshot);
            return NULL;
        }
        if (contained == 0)
        {
            int installed = PyDict_Contains(self->class_template_cache, key);
            if (installed < 0)
            {
                Py_DECREF(snapshot);
                return nullptr;
            }
            if (installed)
            {
                Py_DECREF(snapshot);
                PyErr_SetString(PyExc_RuntimeError,
                                "IDL configuration must be registered before template installation");
                return nullptr;
            }
            added++;
        }
        else if (PyDict_GetItemWithError(self->class_configs, key) != value)
        {
            Py_DECREF(snapshot);
            PyErr_SetString(PyExc_RuntimeError, "IDL configuration is already registered for this type");
            return nullptr;
        }
    }
    if (added == 0)
    {
        Py_DECREF(snapshot);
        return PyLong_FromLong(0);
    }
    PyObject *updated = PyDict_Copy(self->class_configs);
    if (updated == nullptr || PyDict_Update(updated, snapshot) < 0)
    {
        Py_XDECREF(updated);
        Py_DECREF(snapshot);
        return nullptr;
    }
    Py_DECREF(snapshot);
    Py_DECREF(self->class_configs);
    self->class_configs = updated;
    return PyLong_FromSsize_t(added);
}
PyObject *
context_declare_lazy(context_c *self, PyObject *args, PyObject *kwargs)
{
    PyObject* names = NULL;
    PyObject* skip = Py_None;
    static const char *keywords[] = {"names", "skip", NULL};
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "O|O", (char **) keywords,
                                    &names, &skip) < 0) {
        return NULL;
    }

    IsolateState* isolate_state = context_require_isolate(self);
    if (isolate_state == nullptr)
        return NULL;
    IN_ISOLATE(isolate_state);
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();

    // Names the caller already installed as concrete properties. Testing
    // membership here avoids a global property lookup per name, which would
    // otherwise invoke Python interceptors for every declared interface.
    PyObject* skip_set = NULL;
    if (skip != Py_None) {
        PyObject* skip_seq = PySequence_Fast(skip, "declare_lazy skip must be a sequence");
        PyErr_PROPAGATE(skip_seq);
        skip_set = PySet_New(skip_seq);
        Py_DECREF(skip_seq);
        PyErr_PROPAGATE(skip_set);
    }

    PyObject* sequence = PySequence_Fast(names, "declare_lazy requires a sequence of interface names");
    if (sequence == NULL) {
        Py_XDECREF(skip_set);
        return NULL;
    }
    Py_ssize_t size = PySequence_Size(sequence);
    if (size < 0) {
        Py_DECREF(sequence);
        Py_XDECREF(skip_set);
        return NULL;
    }

    Py_ssize_t declared = 0;
    for (Py_ssize_t i = 0; i < size; i++)
    {
        PyObject* item = PySequence_GetItem(sequence, i);
        if (item == NULL) {
            Py_DECREF(sequence);
            Py_XDECREF(skip_set);
            return NULL;
        }
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError,
                         "declare_lazy names must be strings, got a non-string at index %zd",
                         i);
            Py_DECREF(item);
            Py_DECREF(sequence);
            Py_XDECREF(skip_set);
            return NULL;
        }
        bool skipped = false;
        if (skip_set != NULL) {
            int contained = PySet_Contains(skip_set, item);
            if (contained < 0) {
                Py_DECREF(item);
                Py_DECREF(sequence);
                Py_XDECREF(skip_set);
                return NULL;
            }
            skipped = contained == 1;
        }
        const char* text = PyUnicode_AsUTF8_abi3(item);
        if (!skipped && text != nullptr && PySet_Add(self->lazy_names, item) < 0) {
            Py_DECREF(item);
            Py_DECREF(sequence);
            Py_XDECREF(skip_set);
            return nullptr;
        }
        Py_DECREF(item);
        if (text == nullptr) {
            Py_DECREF(sequence);
            Py_XDECREF(skip_set);
            return NULL;
        }
        if (skipped) {
            continue;
        }
        bindings::InstallExposedConstruct(context, global, text);
        declared++;
    }
    Py_DECREF(sequence);
    Py_XDECREF(skip_set);
    return PyLong_FromSsize_t(declared);
}

PyObject *
context_expose_module(context_c *self, PyObject *module)
{
    if (!PyModule_Check(module)) {
        PyErr_SetString(PyExc_TypeError, "context_expose_module requires a module");
        return NULL;
    }

    PyObject *module_all_slow = PyObject_Dir(module);
    PyErr_PROPAGATE(module_all_slow);
    PyObject *module_all = PySequence_Fast(module_all_slow, "o noes");
    Py_XDECREF(module_all_slow);
    PyErr_PROPAGATE(module_all);
    PyObject *members = PyDict_New();
    PyErr_PROPAGATE(members);
    Py_ssize_t module_all_size = PySequence_Size(module_all);
    for (int i = 0; i < module_all_size; i++)
    {
        PyObject *name = PySequence_GetItem(module_all, i);
        if (!PyString_StartsWithString(name, "_"))
        {
            PyObject *value = PyObject_GetAttr(module, name);
            if (value == NULL)
            {
                Py_DECREF(name);
                Py_XDECREF(members);
                return NULL;
            }
            if (PyDict_SetItem(members, name, value) < 0)
            {
                Py_DECREF(name);
                Py_DECREF(value);
                Py_XDECREF(members);
                return NULL;
            }
            Py_DECREF(value);
        }
        Py_DECREF(name);
    }

    PyObject *no_args = PyTuple_New(0);
    PyErr_PROPAGATE(no_args);
    PyObject *result = context_expose(self, no_args, members);
    Py_XDECREF(no_args);
    Py_XDECREF(members);
    return result;
}

PyObject* 
context_js_obj(context_c* self, PyObject* py_object)
{
    PyObject* jsobj = PyObject_GetItem(self->js_object_cache, py_object);
    if (jsobj != NULL) {
        return jsobj;
    }
    if (!PyErr_ExceptionMatches(PyExc_KeyError) &&
        !PyErr_ExceptionMatches(PyExc_TypeError))
        return NULL;
    PyErr_Clear();
    PYV8_RETURN_NONE;
}

PyObject *
context_run(context_c *self, PyObject *args, PyObject *kwargs)
{
    IsolateState* isolate_state = context_require_isolate(self);
    if (isolate_state == nullptr)
        return NULL;
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
        Py_INCREF(program);
    }
    assert(PyObject_IsInstance(program, (PyObject*)script_type));
    script_c *py_script = (script_c *) program;

    if (py_script->isolate_state != isolate_state) {
        Py_DECREF(program);
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return NULL;
    }

    IN_ISOLATE(isolate_state);
    IN_CONTEXT(self->js_context.Get(isolate));
    JS_TRY

    Local<UnboundScript> unbound_script = py_script->script.Get(isolate);
    Py_XDECREF(program);
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


Local<Object> 
context_get_cached_jsobject(Local<Context> js_context, PyObject *py_object)
{
    Isolate* isolate = v8_compat::context_isolate(js_context);
    EscapableHandleScope hs(isolate);
    context_c *self = (context_c *) v8_compat::external_value(js_context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
    js_object *jsobj = (js_object *) PyObject_GetItem(
        self->js_object_cache, py_object);
    if (jsobj != NULL) {
        Local<Object> cached_object = jsobj->object.Get(isolate);
        Py_DECREF((PyObject*)jsobj);
        return hs.Escape(cached_object);
    }
    if (PyErr_ExceptionMatches(PyExc_KeyError) ||
        PyErr_ExceptionMatches(PyExc_TypeError))
        PyErr_Clear();
    return Local<Object>();
}

void 
context_set_cached_jsobject(Local<Context> js_context, PyObject *py_object, Local<Object> object)
{
    Isolate* isolate = v8_compat::context_isolate(js_context);
    EscapableHandleScope hs(isolate);
    context_c *self = (context_c *) v8_compat::external_value(js_context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
    js_object *jsobj = js_object_weak_new(object, js_context);
    if (jsobj == NULL) {
        return;
    }
    if (PyObject_SetItem(self->js_object_cache, py_object, (PyObject *) jsobj) < 0) {
        if (PyErr_ExceptionMatches(PyExc_TypeError)) {
            // if it's a type error, it's probably "cannot create weak reference" and should be ignored.
            PyErr_Clear();
        } else {
            PyErr_WriteUnraisable(PyUnicode_InternFromString("pyv8env py_class_create_js_object setitem"));
        }
    }
    Py_DECREF((PyObject *)jsobj);
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
    IsolateState* isolate_state = context_require_isolate(self);
    if (isolate_state == nullptr)
        return NULL;
    IN_ISOLATE(isolate_state);
    Local<Context> context = self->js_context.Get(isolate);
    PyObject* pyobj = py_from_js(
        v8_compat::global_template_object(context), context);
    PyErr_PROPAGATE(pyobj);
    PyObject* result = context_js_obj(self, pyobj);
    Py_DECREF(pyobj);
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
    return PyObject_GetAttr(global, name);
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
        Py_XDECREF(ctx_value);
    }
    // if the property is defined by Context, delegate
    return PyObject_GenericSetAttr((PyObject *) self, name, value);
}

int 
context_setitem(context_c *self, PyObject *name, PyObject *value)
{
    PyObject *global = context_get_global(self, NULL);
    PyErr_PROPAGATE_(global);
    return PyObject_SetAttr(global, name, value);
}

PyObject *
context_gc(context_c *self) 
{
    IsolateState* isolate_state = context_require_isolate(self);
    if (isolate_state == nullptr)
        return NULL;
    IN_ISOLATE(isolate_state);
    isolate->RequestGarbageCollectionForTesting(Isolate::GarbageCollectionType::kFullGarbageCollection);
    PYV8_RETURN_NONE;
}
