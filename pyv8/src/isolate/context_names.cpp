#include "bridge_base.h"
#include <Python.h>
#include "execution.h"
#include <v8.h>

#include "context.h"
#include "convert.h"
#include "idl_spec.h"
#include "pyclass.h"
#include "v8tools.h"
#include "trace.h"
#include "callback_boundary.h"
#include "pyclass_cross_origin_handlers.h"

using namespace v8;

// Naming things in a realm: the registry it keeps per interface name, and the
// verbs that put a value on its global.

//: An entry is [template, spec, state], and a slot stays NULL until the realm
//: learns that fact, so an entry holds what it knows and nothing more.
PyObject *
context_registry_get(context_c *self, PyObject *name, int slot)
{
    if (self->registry == NULL || !PyUnicode_Check(name))
        return NULL;
    PyObject* entry = PyDict_GetItemWithError(self->registry, name);
    if (entry == NULL)
        return NULL;
    return PyList_GetItem(entry, slot);
}

int
context_registry_set(context_c *self, PyObject *name, int slot, PyObject *value)
{
    if (self->registry == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is no longer active");
        return -1;
    }
    if (!PyUnicode_Check(name)) {
        PyErr_SetString(PyExc_TypeError, "an interface name must be a string");
        return -1;
    }
    PyObject* entry = PyDict_GetItemWithError(self->registry, name);
    bool created = false;
    if (entry == NULL)
    {
        if (PyErr_Occurred())
            return -1;
        entry = PyList_New(REGISTRY_SLOTS);
        if (entry == NULL)
            return -1;
        created = true;
        if (PyDict_SetItem(self->registry, name, entry) < 0) {
            Py_DECREF((PyObject*)entry);
            return -1;
        }
    }
    int written = PyList_SetItem(entry, slot, PyV8_NewRef(value));
    if (created)
        Py_DECREF((PyObject*)entry);
    if (written < 0)
        return -1;
    return 0;
}

static int
name_state(context_c *self, PyObject *name, long value)
{
    PyObject* state = PyLong_FromLong(value);
    PyErr_PROPAGATE_(state);
    int written = context_registry_set(self, name, REGISTRY_STATE, state);
    Py_DECREF((PyObject*)state);
    return written;
}

bool
context_registry_askable(context_c *self, PyObject *name)
{
    if (self->lazy_factory == NULL || self->registry == NULL || !PyUnicode_Check(name))
        return false;
    PyObject* entry = PyDict_GetItemWithError(self->registry, name);
    if (entry == NULL)
        return !PyErr_Occurred();
    PyObject* state = PyList_GetItem(entry, REGISTRY_STATE);
    if (state != NULL && PyLong_AsLong(state) != REGISTRY_IDLE)
        return false;
    // A name that holds a spec but no template still needs its class, so having a spec
    // is not an answer: only a built template is.
    return PyList_GetItem(entry, REGISTRY_TEMPLATE) == NULL;
}

//: Keep the specs an answer carries.  A name whose template is already built
//: keeps the spec that built it, so a later answer cannot rewrite it.
static int
record_specs(context_c *self, PyObject *mapping)
{
    if (!PyDict_Check(mapping) && !PyMapping_Check(mapping)) {
        PyErr_SetString(PyExc_TypeError, "specs must map interface names to IdlSpec objects");
        return -1;
    }
    PyObject* items = PyMapping_Items(mapping);
    PyErr_PROPAGATE_(items);
    for (Py_ssize_t index = 0; index < PyList_Size(items); ++index)
    {
        PyObject* pair = PyList_GetItem(items, index);
        PyObject* key = pair == NULL ? NULL : PyTuple_GetItem(pair, 0);
        PyObject* value = pair == NULL ? NULL : PyTuple_GetItem(pair, 1);
        if (key == NULL || value == NULL
            || !PyUnicode_Check(key) || Py_TYPE(value) != idl_spec_type) {
            PyErr_SetString(PyExc_TypeError, "specs must map interface names to IdlSpec objects");
            Py_DECREF((PyObject*)items);
            return -1;
        }
        if (context_registry_get(self, key, REGISTRY_TEMPLATE) != NULL
            || context_registry_get(self, key, REGISTRY_SPEC) != NULL)
            continue;
        if (context_registry_set(self, key, REGISTRY_SPEC, value) < 0) {
            Py_DECREF((PyObject*)items);
            return -1;
        }
    }
    Py_DECREF((PyObject*)items);
    return 0;
}

//: The realm's answer for one name: a class, or (class, {name: spec}) when the
//: realm holds the overlay itself.  A refused or failed attempt is asked again.
PyObject *
context_ask_factory(context_c *self, PyObject *name)
{
    if (name_state(self, name, REGISTRY_RESOLVING) < 0)
        return NULL;
    PyObject* answer = PyObject_CallFunctionObjArgs(self->lazy_factory, name, NULL);
    if (answer == NULL) {
        name_state(self, name, REGISTRY_IDLE);
        return NULL;
    }
    PyObject* cls = answer;
    PyObject* specs = NULL;
    PyObject* shared = Py_False;
    if (PyTuple_Check(answer) && (PyTuple_Size(answer) == 2 || PyTuple_Size(answer) == 3)) {
        cls = PyTuple_GetItem(answer, 0);
        specs = PyTuple_GetItem(answer, 1);
        if (PyTuple_Size(answer) == 3)
            shared = PyTuple_GetItem(answer, 2);
    }
    if (cls != Py_None && !PyType_Check(cls)) {
        PyErr_SetString(PyExc_TypeError,
                        "lazy factory must answer a class, (class, specs) or None");
        Py_DECREF((PyObject*)answer);
        name_state(self, name, REGISTRY_IDLE);
        return NULL;
    }
    if (cls == Py_None)
        specs = NULL;
    if (specs != NULL && record_specs(self, specs) < 0) {
        Py_DECREF((PyObject*)answer);
        name_state(self, name, REGISTRY_IDLE);
        return NULL;
    }
    // A factory may answer a per-interface sharing map: ancestors can have
    // realm-bound specs even when the requested child is realm-independent.
    if (specs != NULL && PyDict_Check(specs)) {
        PyObject *key = nullptr;
        PyObject *value = nullptr;
        Py_ssize_t position = 0;
        while (PyDict_Next(specs, &position, &key, &value)) {
            PyObject* share_key = PyDict_Check(shared)
                ? PyDict_GetItemWithError(shared, key) : shared;
            if (share_key == nullptr && PyErr_Occurred()) {
                Py_DECREF((PyObject*)answer);
                name_state(self, name, REGISTRY_IDLE);
                return NULL;
            }
            if (context_registry_set(self, key, REGISTRY_SHARED,
                                     share_key == Py_True ? Py_True : Py_False) < 0) {
                Py_DECREF((PyObject*)answer);
                name_state(self, name, REGISTRY_IDLE);
                return NULL;
            }
        }
    }
    PyObject* share_name = PyDict_Check(shared)
        ? PyDict_GetItemWithError(shared, name) : shared;
    if (share_name == nullptr && PyErr_Occurred()) {
        Py_DECREF((PyObject*)answer);
        name_state(self, name, REGISTRY_IDLE);
        return NULL;
    }
    if (context_registry_set(self, name, REGISTRY_SHARED,
                             share_name == Py_True ? Py_True : Py_False) < 0) {
        Py_DECREF((PyObject*)answer);
        name_state(self, name, REGISTRY_IDLE);
        return NULL;
    }
    name_state(self, name, REGISTRY_ASKED);
    if (cls == Py_None) {
        Py_DECREF((PyObject*)answer);
        return PyV8_NewRef(Py_None);
    }
    Py_INCREF(cls);
    Py_DECREF((PyObject*)answer);
    return cls;
}

PyObject *
context_expose(context_c *self, PyObject *args, PyObject *kwargs)
{
    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();

    args = PySequence_Fast(args, "sequence required");
    PyErr_PROPAGATE(args);
    Py_ssize_t args_size = PySequence_Size(args);
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        if (object == NULL) {
            Py_DECREF((PyObject*)args);
            return NULL;
        }
        if (!PyObject_HasAttrString(object, "__name__"))
        {
            PyErr_SetString(PyExc_TypeError, "Object passed to expose must have a __name__");
            Py_DECREF((PyObject*)object);
            Py_DECREF((PyObject*)args);
            return NULL;
        }
        Py_DECREF((PyObject*)object);
    }
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        if (object == NULL) {
            Py_DECREF((PyObject*)args);
            return NULL;
        }
        PyObject *name = PyObject_GetAttrString(object, "__name__");
        if (name == NULL) {
            Py_DECREF((PyObject*)object);
            Py_DECREF((PyObject*)args);
            return NULL;
        }
        Local<Value> js_name = js_from_py(name, context);
        Local<Value> js_object = js_from_py(object, context);
        if (js_name.IsEmpty() || js_object.IsEmpty() || PyErr_Occurred()) {
            Py_DECREF((PyObject*)object);
            Py_DECREF((PyObject*)name);
            Py_DECREF((PyObject*)args);
            return NULL;
        }
        bool created;
        if (!global->CreateDataProperty(
                context, js_name.As<String>(), js_object).To(&created)) {
            Py_DECREF((PyObject*)object);
            Py_DECREF((PyObject*)name);
            Py_DECREF((PyObject*)args);
            PyErr_SetString(PyExc_RuntimeError, "failed to expose Python object");
            return NULL;
        }
        Py_DECREF((PyObject*)object);
        Py_DECREF((PyObject*)name);
    }
    Py_XDECREF((PyObject*)args);

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

//: Install one global the caller already has a value for, with the attributes it names.
PyObject *
context_define_global(context_c *self, PyObject *args, PyObject *kwargs)
{
    PyObject* name = NULL;
    PyObject* value = NULL;
    unsigned int attributes = 0;
    static const char *keywords[] = {"name", "value", "attributes", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|I", (char **) keywords,
                                    &name, &value, &attributes)) {
        return NULL;
    }
    if (PyUnicode_Check(name) == 0) {
        PyErr_SetString(PyExc_TypeError, "define_global takes a name string");
        return NULL;
    }

    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();
    Local<Value> js_name = js_from_py(name, context);
    Local<Value> js_value = js_from_py(value, context);
    if (js_name.IsEmpty() || js_value.IsEmpty() || PyErr_Occurred())
        return NULL;
    if (global->DefineOwnProperty(context, js_name.As<String>(), js_value,
                                  static_cast<PropertyAttribute>(attributes)).IsNothing()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to define the global property");
        return NULL;
    }
    PYV8_RETURN_NONE;
}

//: Install each name as a non-enumerable lazy constructor on the realm's global.
PyObject *
context_install(context_c *self, PyObject *names)
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();

    PyObject* sequence = PySequence_Fast(names, "install requires a sequence of interface names");
    if (sequence == NULL)
        return NULL;
    Py_ssize_t size = PySequence_Size(sequence);
    if (size < 0) {
        Py_DECREF((PyObject*)sequence);
        return NULL;
    }

    Py_ssize_t declared = 0;
    for (Py_ssize_t i = 0; i < size; i++)
    {
        PyObject* item = PySequence_GetItem(sequence, i);
        if (item == NULL) {
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError,
                         "install names must be strings, got a non-string at index %zd", i);
            Py_DECREF((PyObject*)item);
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        const char* text = PyUnicode_AsUTF8_abi3(item);
        Py_DECREF((PyObject*)item);
        if (text == nullptr) {
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        bindings::InstallExposedConstruct(context, global, text);
        declared++;
    }
    Py_DECREF((PyObject*)sequence);
    return PyLong_FromSsize_t(declared);
}

//: Install the global interface's context-dependent members on the built global.
//: This is Blink's conditional phase: it runs after the host globals (``chrome``)
//: so those stay ahead of ``caches`` and the rest of the Window tail.
PyObject *
context_install_late(context_c *self, PyObject *ignored)
{
    (void)ignored;
    py_class* klass = self->global_template == nullptr
        ? nullptr : (py_class*)self->global_template;
    if (klass == nullptr || klass->idl_spec == nullptr)
        return PyLong_FromLong(0);
    //: Assembling the realm is host work, like installing a global value.
    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = v8_compat::global_template_object(context);
    IdlSpec* spec = (IdlSpec*)klass->idl_spec;
    const char* world = klass->cls_name == nullptr
        ? "" : PyUnicode_AsUTF8_abi3(klass->cls_name);
    if (world == nullptr) return nullptr;
    Local<Signature> signature;
    Py_ssize_t installed = 0;
    if (spec->late_attributes != nullptr) {
        if (!bindings::InstallAttributes(isolate, world, context, global, signature,
                                         klass->cls, spec->late_attributes,
                                         klass->operation_callback_data, self, false))
            return nullptr;
        installed += PyTuple_Size(spec->late_attributes);
    }
    return PyLong_FromSsize_t(installed);
}

//: The conditional operations and the template-late attributes follow the
//: conditional constructors, which the realm installs between the two calls.
PyObject *
context_install_late_methods(context_c *self, PyObject *ignored)
{
    (void)ignored;
    py_class* klass = self->global_template == nullptr
        ? nullptr : (py_class*)self->global_template;
    if (klass == nullptr || klass->idl_spec == nullptr)
        return PyLong_FromLong(0);
    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = v8_compat::global_template_object(context);
    IdlSpec* spec = (IdlSpec*)klass->idl_spec;
    const char* world = klass->cls_name == nullptr
        ? "" : PyUnicode_AsUTF8_abi3(klass->cls_name);
    if (world == nullptr) return nullptr;
    Local<Signature> signature;
    Py_ssize_t installed = 0;
    if (spec->late_methods != nullptr) {
        if (!bindings::InstallOperations(isolate, world, context, global, signature,
                                         klass->cls, spec->late_methods,
                                         klass->operation_callback_data, self, false))
            return nullptr;
        installed += PyTuple_Size(spec->late_methods);
    }
    if (spec->late_template_attributes != nullptr) {
        if (!bindings::InstallAttributes(isolate, world, context, global, signature,
                                         klass->cls, spec->late_template_attributes,
                                         klass->operation_callback_data, self, false))
            return nullptr;
        installed += PyTuple_Size(spec->late_template_attributes);
    }
    return PyLong_FromSsize_t(installed);
}

PyObject *
context_install_tail(context_c *self, PyObject *ignored)
{
    (void)ignored;
    py_class* klass = self->global_template == nullptr
        ? nullptr : (py_class*)self->global_template;
    if (klass == nullptr || klass->idl_spec == nullptr)
        return PyLong_FromLong(0);
    IdlSpec* spec = (IdlSpec*)klass->idl_spec;
    if (spec->tail_methods == nullptr || PyTuple_Size(spec->tail_methods) == 0)
        return PyLong_FromLong(0);
    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = v8_compat::global_template_object(context);
    const char* world = klass->cls_name == nullptr
        ? "" : PyUnicode_AsUTF8_abi3(klass->cls_name);
    if (world == nullptr) return nullptr;
    Local<Signature> signature;
    if (!bindings::InstallOperations(isolate, world, context, global, signature,
                                     klass->cls, spec->tail_methods,
                                     klass->operation_callback_data, self, false))
        return nullptr;
    return PyLong_FromSsize_t(PyTuple_Size(spec->tail_methods));
}

PyObject *
context_hide_global(context_c *self, PyObject *name)
{
    trace::TraceSilence silence(self);
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Context> context = self->js_context.Get(isolate);
    Local<Value> js_name = js_from_py(name, context);
    if (js_name.IsEmpty() || PyErr_Occurred()) return nullptr;
    if (!js_name->IsString()) {
        PyErr_SetString(PyExc_TypeError, "hide_global requires a name");
        return nullptr;
    }
    Local<Object> global = context->Global();
    bool present = false;
    if (!global->HasOwnProperty(context, js_name.As<String>()).To(&present)) return nullptr;
    if (!present) return PyBool_FromLong(0);
    // CreateDataProperty made the global configurable, so the name can leave again.
    bool deleted = global->Delete(context, js_name.As<String>()).FromMaybe(false);
    return PyBool_FromLong(deleted ? 1 : 0);
}


void context_install_access_check(v8::Isolate* isolate)
{
    isolate->SetFailedAccessCheckCallbackFunction(
        CallbackBoundary<cross_origin_failed_access>::call);
}

v8::Local<v8::Function> context_own_constructor(v8::Local<v8::Context> context, const char* shown)
{
    context_c* owner = context_from_v8(context);
    PyObject* name = owner == nullptr ? nullptr : PyUnicode_FromString(shown);
    if (name == nullptr)
    {
        PyErr_Clear();
        return Local<Function>();
    }
    PyObject* interface_template = context_registry_get(owner, name, REGISTRY_TEMPLATE);
    Py_DECREF(name);
    PyErr_Clear();
    if (interface_template == nullptr)
        return Local<Function>();
    auto* klass = (py_class*)interface_template;
    Local<Value> interface = py_class_get_constructor(context, klass, (py_class*)klass->parent);
    if (interface.IsEmpty() || !interface->IsFunction())
    {
        PyErr_Clear();
        return Local<Function>();
    }
    return interface.As<Function>();
}
