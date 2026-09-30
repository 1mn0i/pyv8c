#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "context.h"
#include "convert.h"
#include "idl_spec.h"
#include "pyclass.h"
#include "v8tools.h"
#include "trace.h"

using namespace v8;

// Installing names into a realm: what `expose`, `declare_lazy` and a module's
// `__all__` do, and what a later realm-specific overlay changes.  This is the one
// part of a context's life that talks to the interface layer, which is why it
// lives on its own rather than in the middle of the lifecycle.

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

PyObject *context_update_configs(context_c *self, PyObject *mapping)
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
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
        Py_DECREF((PyObject*)snapshot);
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
            Py_DECREF((PyObject*)snapshot);
            return NULL;
        }
        if (contained == 0)
        {
            int installed = PyDict_Contains(self->class_template_cache, key);
            if (installed < 0)
            {
                Py_DECREF((PyObject*)snapshot);
                return nullptr;
            }
            if (installed)
            {
                Py_DECREF((PyObject*)snapshot);
                PyErr_SetString(PyExc_RuntimeError,
                                "IDL configuration must be registered before template installation");
                return nullptr;
            }
            added++;
        }
        else if (PyDict_GetItemWithError(self->class_configs, key) != value)
        {
            Py_DECREF((PyObject*)snapshot);
            PyErr_SetString(PyExc_RuntimeError, "IDL configuration is already registered for this type");
            return nullptr;
        }
    }
    if (added == 0)
    {
        Py_DECREF((PyObject*)snapshot);
        return PyLong_FromLong(0);
    }
    PyObject *updated = PyDict_Copy(self->class_configs);
    if (updated == nullptr || PyDict_Update(updated, snapshot) < 0)
    {
        Py_XDECREF((PyObject*)updated);
        Py_DECREF((PyObject*)snapshot);
        return nullptr;
    }
    Py_DECREF((PyObject*)snapshot);
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

    ExecutionScope execution(self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
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
        Py_DECREF((PyObject*)skip_seq);
        PyErr_PROPAGATE(skip_set);
    }

    PyObject* sequence = PySequence_Fast(names, "declare_lazy requires a sequence of interface names");
    if (sequence == NULL) {
        Py_XDECREF((PyObject*)skip_set);
        return NULL;
    }
    Py_ssize_t size = PySequence_Size(sequence);
    if (size < 0) {
        Py_DECREF((PyObject*)sequence);
        Py_XDECREF((PyObject*)skip_set);
        return NULL;
    }

    Py_ssize_t declared = 0;
    for (Py_ssize_t i = 0; i < size; i++)
    {
        PyObject* item = PySequence_GetItem(sequence, i);
        if (item == NULL) {
            Py_DECREF((PyObject*)sequence);
            Py_XDECREF((PyObject*)skip_set);
            return NULL;
        }
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError,
                         "declare_lazy names must be strings, got a non-string at index %zd",
                         i);
            Py_DECREF((PyObject*)item);
            Py_DECREF((PyObject*)sequence);
            Py_XDECREF((PyObject*)skip_set);
            return NULL;
        }
        bool skipped = false;
        if (skip_set != NULL) {
            int contained = PySet_Contains(skip_set, item);
            if (contained < 0) {
                Py_DECREF((PyObject*)item);
                Py_DECREF((PyObject*)sequence);
                Py_XDECREF((PyObject*)skip_set);
                return NULL;
            }
            skipped = contained == 1;
        }
        const char* text = PyUnicode_AsUTF8_abi3(item);
        if (!skipped && text != nullptr && PySet_Add(self->materialisable_names, item) < 0) {
            Py_DECREF((PyObject*)item);
            Py_DECREF((PyObject*)sequence);
            Py_XDECREF((PyObject*)skip_set);
            return nullptr;
        }
        Py_DECREF((PyObject*)item);
        if (text == nullptr) {
            Py_DECREF((PyObject*)sequence);
            Py_XDECREF((PyObject*)skip_set);
            return NULL;
        }
        if (skipped) {
            continue;
        }
        bindings::InstallExposedConstruct(context, global, text);
        declared++;
    }
    Py_DECREF((PyObject*)sequence);
    Py_XDECREF((PyObject*)skip_set);
    return PyLong_FromSsize_t(declared);
}

PyObject *
context_declare_materialisable(context_c *self, PyObject *names)
{
    ExecutionScope execution(self);
    if (!execution) return nullptr;
    if (self->materialisable_names == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "V8 context is no longer active");
        return NULL;
    }
    PyObject* sequence = PySequence_Fast(names, "declare_materialisable requires a sequence of interface names");
    if (sequence == NULL)
        return NULL;
    Py_ssize_t size = PySequence_Size(sequence);
    if (size < 0) {
        Py_DECREF((PyObject*)sequence);
        return NULL;
    }
    Py_ssize_t recorded = 0;
    for (Py_ssize_t i = 0; i < size; i++)
    {
        PyObject* item = PySequence_GetItem(sequence, i);
        if (item == NULL) {
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError,
                         "declare_materialisable names must be strings, got a non-string at index %zd",
                         i);
            Py_DECREF((PyObject*)item);
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        if (PySet_Add(self->materialisable_names, item) < 0) {
            Py_DECREF((PyObject*)item);
            Py_DECREF((PyObject*)sequence);
            return NULL;
        }
        Py_DECREF((PyObject*)item);
        recorded++;
    }
    Py_DECREF((PyObject*)sequence);
    return PyLong_FromSsize_t(recorded);
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
    Py_XDECREF((PyObject*)module_all_slow);
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
                Py_DECREF((PyObject*)name);
                Py_XDECREF((PyObject*)members);
                return NULL;
            }
            if (PyDict_SetItem(members, name, value) < 0)
            {
                Py_DECREF((PyObject*)name);
                Py_DECREF((PyObject*)value);
                Py_XDECREF((PyObject*)members);
                return NULL;
            }
            Py_DECREF((PyObject*)value);
        }
        Py_DECREF((PyObject*)name);
    }

    PyObject *no_args = PyTuple_New(0);
    PyErr_PROPAGATE(no_args);
    PyObject *result = context_expose(self, no_args, members);
    Py_XDECREF((PyObject*)no_args);
    Py_XDECREF((PyObject*)members);
    return result;
}
