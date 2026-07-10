
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "context.h"
#include "jsfunction.h"
#include "jspromise.h"
#include "jstypedarray.h"

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
    IN_V8;
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
        self->object.Reset(isolate, object);
    }
    return self;
}

void 
js_object_weak_callback(const WeakCallbackInfo<js_object> &info)
{
    js_object *self = info.GetParameter();
    self->object.Reset();
    Py_XDECREF(self);
}

js_object *
js_object_weak_new(Local<Object> object, Local<Context> context)
{
    IN_V8;
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
        self->object.Reset(isolate, object);
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
    PyObject *attr_value = PyObject_GenericGetAttr((PyObject *)self, name);
    if (attr_value == NULL)
        PyErr_Clear();
    else
        return attr_value;

    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    Local<Value> js_name = js_from_py(name, context);
    JS_TRY
    if (!context_setup_timeout(context)) return NULL;
    if (!object->Has(context, js_name).FromJust()) {
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
    PY_PROPAGATE_JS;

    MaybeLocal<Value> js_value = object->Get(context, js_name);
    if (!context_cleanup_timeout(context)) return NULL;

    PY_PROPAGATE_JS;
    PyObject *value = py_from_js(js_value.ToLocalChecked(), context);
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

    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY


    if (!context_setup_timeout(context)) return -1;

    if (value != NULL)
        object->Set(context, js_from_py(name, context), js_from_py(value, context));
    else
        object->Delete(context, js_from_py(name, context));

    if (!context_cleanup_timeout(context)) return -1;

    PY_PROPAGATE_JS_RET(-1);
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
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    Local<Context> context = object->GetCreationContextChecked();
    Context::Scope cs(context);
    JS_TRY

    MaybeLocal<Array> maybe_properties = object->GetOwnPropertyNames(context, ALL_PROPERTIES);
    PY_PROPAGATE_JS;
    Local<Array> properties = maybe_properties.ToLocalChecked();
    PyObject *py_properties = PyList_New(properties->Length());
    PyErr_PROPAGATE(py_properties);

    if (!context_setup_timeout(context)) return NULL;
    for (unsigned i = 0; i < properties->Length(); i++) {
        MaybeLocal<Value> js_property = properties->Get(context, i);
        PY_PROPAGATE_JS;
        PyObject *py_property = py_from_js(js_property.ToLocalChecked(), context);
        PyList_SetItem(py_properties, i, py_property);
    }

    if (!context_cleanup_timeout(context)) return NULL;

    return py_properties;
}

PyObject * 
js_object_eq(js_object* self, PyObject* other)
{
    if (!PyObject_TypeCheck(other, js_object_type)) {
        Py_RETURN_FALSE;
    }

    IN_V8;
    JS_TRY;

    Local<Object> js_obj = self->object.Get(isolate);
    Local<Object> js_obj_other = ((js_object*)other)->object.Get(isolate);
    if (js_obj_other.IsEmpty() || js_obj.IsEmpty() || !js_obj->StrictEquals(js_obj_other)) {
        Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

PyObject *
js_object_repr(js_object *self)
{
    IN_V8;
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
    self->object.Reset();
    TP_FREE_ABI3(js_object_type, self);
}

