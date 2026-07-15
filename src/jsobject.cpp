
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "context.h"
#include "jsfunction.h"
#include "jspromise.h"
#include "jstypedarray.h"
#include "timeout_guard.h"

using namespace v8;


PyMethodDef js_object_methods[] = {
    {"__dir__", (PyCFunction)js_object_dir, METH_NOARGS, NULL},
    {NULL} };


static PyType_Slot js_object_slots[] = {
    {Py_tp_getattro, (void*)js_object_getattro},    // __getattribute__
    {Py_tp_setattro, (void*)js_object_setattro},    // __setattribute__
    {Py_mp_subscript, (void*)js_object_getattro},  // __getitem__
    {Py_mp_ass_subscript, (void*)js_object_setattro},  // __setitem__
    {Py_tp_iter, (void*)js_object_getiter},
    {Py_tp_richcompare, (void*)js_object_eq},
    {Py_tp_dealloc, (void*)js_object_dealloc},
    {Py_tp_repr, (void*)js_object_repr},
    {Py_tp_methods, js_object_methods},
    {Py_tp_doc, "v8 Object"},
    {0, NULL} };

static PyType_Spec js_object_spec = {
    "_pyv8.JSObject",
    sizeof(js_object),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    js_object_slots };


PyTypeObject *js_object_type = NULL;

static bool
js_object_should_try_python_attribute(js_object* self, PyObject* name)
{
    if (Py_TYPE(self) != js_object_type &&
        Py_TYPE(self) != js_function_type &&
        Py_TYPE(self) != js_promise_type &&
        Py_TYPE(self) != js_typedarray_type)
    {
        return true;
    }
    if (!PyUnicode_Check(name))
        return false;

    Py_ssize_t length;
    const char* utf8_name = PyUnicode_AsUTF8AndSize(name, &length);
    if (utf8_name == NULL)
        return true;
    return length == 0 || utf8_name[0] == '_';
}

void
js_object_set_persistent(js_object* self, Local<Object> object,
                         Local<Context> context, bool retain_context)
{
    Isolate* isolate = context->GetIsolate();
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    self->isolate_state = isolate_state;
    self->object.Reset(isolate, object);
    isolate_state->retain_v8_handle();
    if (!context.IsEmpty()) {
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            context_c* py_context = (context_c*)context_data.As<External>()->Value();
            if (retain_context) {
                self->py_context = (PyObject*)py_context;
                Py_INCREF(self->py_context);
            }
            if (py_context->py_isolate != nullptr) {
                self->py_isolate = py_context->py_isolate;
                Py_INCREF(self->py_isolate);
            }
        }
    }
}

int js_object_type_init()
{
    js_object_type = (PyTypeObject*)PyType_FromSpec(&js_object_spec);
    if (js_object_type == NULL)
    {
        return -1;
    }
    return 0;
}


js_object *
js_object_new(Local<Object> object, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(context->GetIsolate());
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_object *self;
    
    if (object->IsPromise()) {
        //self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_promise_type, 0);
    } else if (object->IsCallable()) {
        //self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
        self = (js_object *)PyType_GenericAlloc(js_function_type, 0);
    } else if (object->IsTypedArray()) {
        //self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_typedarray_type, 0);
    } else {
        //self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_object_type, 0);
    }

    if (self != NULL) {
        js_object_set_persistent(self, object, context, true);
    }
    return self;
}

void 
js_object_weak_callback(const WeakCallbackInfo<js_object> &info)
{
    js_object *self = info.GetParameter();
    if (!self->object.IsEmpty()) {
        self->object.Reset();
        self->isolate_state->release_v8_handle();
    }
    Py_XDECREF(self);
}

js_object *
js_object_weak_new(Local<Object> object, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(context->GetIsolate());
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_object *self;
    if (object->IsPromise()) {
        //self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_promise_type, 0);
    }
    else if (object->IsCallable()) {
        //self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_function_type, 0);
    }
    else if (object->IsTypedArray()) {
        //self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_typedarray_type, 0);
    }
    else {
        //self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_object_type, 0);
    }

    if (self != NULL) {
        js_object_set_persistent(self, object, context, false);
        // The V8 weak callback owns this reference and releases it on GC.
        Py_INCREF(self);
        //self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kFinalizer);
        self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kParameter);
    }
    return self;
}

PyObject *
js_object_getattro(js_object *self, PyObject *name)
{
    if (js_object_should_try_python_attribute(self, name))
    {
        PyObject *attr_value = PyObject_GenericGetAttr((PyObject *)self, name);
        if (attr_value == NULL)
            PyErr_Clear();
        else
            return attr_value;
    }

    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    Local<Value> js_name = js_from_py(name, context);
    if (PyErr_Occurred()) return NULL;
    if (js_name.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to convert JavaScript property name");
        return NULL;
    }
    JS_TRY
    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return NULL;
    Maybe<bool> maybe_has_property = object->Has(context, js_name);
    if (!timeout_guard.stop()) return NULL;
    PY_PROPAGATE_JS;
    bool has_property;
    if (!maybe_has_property.To(&has_property)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to check JavaScript property");
        return NULL;
    }
    if (!has_property) {
        // TODO fix this so that it works
        PyObject *class_name = py_from_js(object->GetConstructorName(), context);
        PyErr_PROPAGATE(class_name);
        PyObject *class_name_string = PyObject_Str(class_name);
        Py_XDECREF(class_name);
        PyErr_PROPAGATE(class_name_string);

        PyErr_Format(PyExc_AttributeError, "'%.50S' JavaScript object has no attribute '%.400S'", 
                class_name_string, name);
        Py_XDECREF(class_name_string);
        return NULL;
    }

    if (!timeout_guard.start()) return NULL;
    MaybeLocal<Value> js_value = object->Get(context, js_name);
    if (!timeout_guard.stop()) return NULL;

    PY_PROPAGATE_JS;
    Local<Value> local_js_value;
    if (!js_value.ToLocal(&local_js_value)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property");
        return NULL;
    }
    PyObject *value = py_from_js(local_js_value, context);
    PyErr_PROPAGATE(value);
    // if this was called like object.method() then bind the return value to make it callable
    if (Py_TYPE(value) == js_function_type) {
        js_function *func = (js_function *) value;

        func->js_this.Reset(isolate, object);
    }
    return value;
}

int 
js_object_setattro(js_object *self, PyObject *name, PyObject *value)
{
    if (js_object_should_try_python_attribute(self, name))
    {
        PyObject *attr_value = PyObject_GenericGetAttr((PyObject *)self, name);
        if (attr_value == NULL)
        {
            PyErr_Clear();
        }
        else
        {
            Py_XDECREF(attr_value);
            return PyObject_GenericSetAttr((PyObject *)self, name, value);
        }
    }

    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return -1;
    IN_ISOLATE(self->isolate_state);
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    Local<Value> js_name = js_from_py(name, context);
    if (PyErr_Occurred()) return -1;
    Local<Value> js_value;
    if (value != NULL) {
        js_value = js_from_py(value, context);
        if (PyErr_Occurred()) return -1;
    }

    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return -1;

    Maybe<bool> maybe_worked = value != NULL
        ? object->Set(context, js_name, js_value)
        : object->Delete(context, js_name);

    if (!timeout_guard.stop()) return -1;

    PY_PROPAGATE_JS_RET(-1);
    bool worked;
    if (!maybe_worked.To(&worked)) {
        PY_PROPAGATE_JS_RET(-1);
        PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript property");
        return -1;
    }
    return 0;
}

PyObject *
js_object_getiter(js_object *self)
{
    PyObject *props = js_object_dir(self);
    PyErr_PROPAGATE(props);
    PyObject *iter = PySeqIter_New(props);
    Py_XDECREF(props);
    return iter;
}

PyObject *
js_object_dir(js_object *self)
{
    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);
    Local<Object> object = self->object.Get(isolate);
    Local<Context> context = object->GetCreationContextChecked();
    Context::Scope cs(context);
    JS_TRY

    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return NULL;
    MaybeLocal<Array> maybe_properties = object->GetOwnPropertyNames(context, ALL_PROPERTIES);
    if (!timeout_guard.stop()) return NULL;
    PY_PROPAGATE_JS;
    Local<Array> properties;
    if (!maybe_properties.ToLocal(&properties)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property names");
        return NULL;
    }
    PyObject *py_properties = PyList_New(properties->Length());
    PyErr_PROPAGATE(py_properties);

    if (!timeout_guard.start()) {
        Py_DECREF(py_properties);
        return NULL;
    }
    for (unsigned i = 0; i < properties->Length(); i++) {
        MaybeLocal<Value> js_property = properties->Get(context, i);
        Local<Value> local_js_property;
        if (!js_property.ToLocal(&local_js_property)) {
            timeout_guard.stop();
            Py_DECREF(py_properties);
            PY_PROPAGATE_JS;
            PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property name");
            return NULL;
        }
        PyObject *py_property = py_from_js(local_js_property, context);
        if (py_property == NULL) {
            timeout_guard.stop();
            Py_DECREF(py_properties);
            return NULL;
        }
        if (PyList_SetItem(py_properties, i, py_property) < 0) {
            timeout_guard.stop();
            Py_DECREF(py_property);
            Py_DECREF(py_properties);
            return NULL;
        }
    }

    if (!timeout_guard.stop()) {
        Py_DECREF(py_properties);
        return NULL;
    }

    return py_properties;
}

PyObject * 
js_object_eq(js_object* self, PyObject* other)
{
    if (!PyObject_TypeCheck(other, js_object_type)) {
        Py_RETURN_FALSE;
    }

    js_object* other_object = (js_object*)other;
    if (self->isolate_state != other_object->isolate_state)
        Py_RETURN_FALSE;
    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);
    JS_TRY;

    Local<Object> js_obj = self->object.Get(isolate);
    Local<Object> js_obj_other = other_object->object.Get(isolate);
    if (js_obj_other.IsEmpty() || js_obj.IsEmpty() || !js_obj->StrictEquals(js_obj_other)) {
        Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

PyObject *
js_object_repr(js_object *self)
{
    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    Local<String> v8_str;
    MaybeLocal<String> maybe_v8_str = object->ToString(context);
    if (!maybe_v8_str.ToLocal(&v8_str)) {
        return PyUnicode_FromString("[error toString object]");
    }

    v8::String::Utf8Value str(isolate, v8_str);
    PyObject* repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;
    return repr;
}

void 
js_object_dealloc(js_object *self)
{
    if (!self->object.IsEmpty()) {
        self->object.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    Py_XDECREF(self->py_context);
    Py_XDECREF(self->py_isolate);
    self->isolate_state = nullptr;
    TP_FREE_ABI3(js_object_type, self);
}
