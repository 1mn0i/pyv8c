#include <Python.h>
#include "pyv8env.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "context.h"

using namespace v8;


enum class ArrayType {
    Uint32Array = 1,
    Uint16Array,
    Uint8Array,
    Int32Array,
    Int16Array,
    Int8Array,
    Float64Array,
    Float32Array,
    Uint8ClampedArray
};


PyTypeObject js_object_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
PyMethodDef js_object_methods[] = {
    {"__dir__", (PyCFunction) js_object_dir, METH_NOARGS, NULL},
    {NULL}
};
PyMappingMethods js_object_mapping_methods = {
    NULL, (binaryfunc) js_object_getattro, (objobjargproc) js_object_setattro
};
int js_object_type_init() {
    js_object_type.tp_name = "pyv8env.Object";
    js_object_type.tp_basicsize = sizeof(js_object);
    js_object_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_object_type.tp_doc = "v8 Object";

    js_object_type.tp_dealloc = (destructor) js_object_dealloc;
    js_object_type.tp_getattro = (getattrofunc) js_object_getattro;
    js_object_type.tp_setattro = (setattrofunc) js_object_setattro;
    js_object_type.tp_iter = (getiterfunc) js_object_getiter;
    js_object_type.tp_repr = (reprfunc) js_object_repr;
    js_object_type.tp_richcompare = (richcmpfunc)js_object_eq;
    js_object_type.tp_methods = js_object_methods;
    js_object_type.tp_as_mapping = &js_object_mapping_methods;
    return PyType_Ready(&js_object_type);
}

PyTypeObject js_promise_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int js_promise_type_init() {
    js_promise_type.tp_name = "pyv8env.Promise";
    js_promise_type.tp_basicsize = sizeof(js_promise);
    js_promise_type.tp_dealloc = (destructor) js_promise_dealloc;
    js_promise_type.tp_new = (newfunc)js_promise_new;
    js_promise_type.tp_repr = (reprfunc)js_promise_repr;
    js_promise_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_promise_type.tp_doc = "v8 Promise";
    js_promise_type.tp_base = &js_object_type;
    return PyType_Ready(&js_promise_type);
}

PyTypeObject js_typedarray_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int js_typedarray_type_init() {
    js_typedarray_type.tp_name = "pyv8env.TypedArray";
    js_typedarray_type.tp_basicsize = sizeof(js_typedarray);
    js_typedarray_type.tp_dealloc = (destructor)js_typedarray_dealloc;
    js_typedarray_type.tp_new = (newfunc)js_typearray_new;
    js_typedarray_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_typedarray_type.tp_doc = "v8 TypedArray";
    js_typedarray_type.tp_repr = (reprfunc)js_typedarray_repr;
    js_typedarray_type.tp_base = &js_object_type;
    return PyType_Ready(&js_typedarray_type);
}

PyObject* js_typearray_new(PyTypeObject* type, PyObject* args, PyObject* kwargs) {
    IN_V8;

    PyObject* data = NULL;
    PyObject* typed = NULL;
    static const char* keywords[] = {"data", "typed", NULL };
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "OO", (char**)keywords,&data, &typed) < 0) {
        return NULL;
    }

    if (!(PyList_Check(data) || PyObject_TypeCheck(data, &js_object_type)) || !PyLong_Check(typed)) {
        return NULL;
    }

    IN_CONTEXT(isolate->GetCurrentContext());
    js_object* self = (js_object*)type->tp_alloc(type, 0);


    v8::Local<v8::ArrayBuffer> array_buffer;

    if (PyObject_TypeCheck(data, &js_object_type)) {
        js_object* py_value = (js_object*)data;
        array_buffer = py_value->object.Get(isolate).As<ArrayBuffer>();
        auto ary = v8::Uint8Array::New(array_buffer, 0, array_buffer->ByteLength());
        self->object.Reset(isolate, ary);
        return (PyObject*)self;
    }

    int length = PyList_GET_SIZE(data);
    ArrayType array_type = static_cast<ArrayType>(PyLong_AsLong(typed));
    if (array_type == ArrayType::Uint32Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(uint32_t));
        auto ary = v8::Uint32Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Uint16Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(uint16_t));
        auto ary = v8::Uint16Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Uint8Array) {

        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(uint8_t));
        auto ary = v8::Uint8Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Int32Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(int32_t));
        auto ary = v8::Int32Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Int16Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(int16_t));
        auto ary = v8::Int16Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Int8Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(int8_t));
        auto ary = v8::Int8Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Float64Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(double));
        auto ary = v8::Float64Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            if (!PyLong_Check(val))
                return NULL;
            ary->Set(isolate->GetCurrentContext(), i, v8::Number::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Float32Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(float));
        auto ary = v8::Float32Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            ary->Set(isolate->GetCurrentContext(), i, v8::Number::New(isolate, PyFloat_AS_DOUBLE(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Uint8ClampedArray) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(uint8_t));
        auto ary = v8::Uint8ClampedArray::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->object.Reset(isolate, ary);
    }else{
            return NULL;
    }
    
    return (PyObject*)self;
}

PyObject* js_promise_new(PyTypeObject* type, PyObject* args, PyObject* kwargs) {
    IN_V8;

    PyObject* resolve = NULL;
    PyObject* reject = NULL;
    static const char* keywords[] = { "resolve", "reject", NULL};
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "|OO", (char**)keywords, &resolve, &reject) < 0) {
        return NULL;
    }
    IN_CONTEXT(isolate->GetCurrentContext());
    js_object* self = (js_object*)type->tp_alloc(type, 0);
    Local<Promise::Resolver> resolver = Promise::Resolver::New(context).ToLocalChecked();
    Local<Promise> promise = resolver->GetPromise();
    if (resolve != NULL) {
        Local<Object> exception = pyexception_to_jsexception(resolve, context);
        if (!exception.IsEmpty()) 
            resolver->Resolve(context, exception).ToChecked();
        else 
            resolver->Resolve(context, js_from_py(resolve, context)).ToChecked();

    }
    if (reject != NULL) {
        Local<Object> exception = pyexception_to_jsexception(reject, context);
        if (!exception.IsEmpty())
            resolver->Reject(context, exception).ToChecked();
        else
            resolver->Reject(context, js_from_py(reject, context)).ToChecked();
    }
    self->object.Reset(isolate, promise);
    return (PyObject*)self;
}

js_object *js_object_new(Local<Object> object, Local<Context> context) {
    IN_V8;
    Context::Scope cs(context);
    js_object *self;
    if (object->IsPromise()) {
        self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
    } else if (object->IsCallable()) {
        self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
    } else if (object->IsTypedArray()) {
        self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
    } else {
        self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
    }

    if (self != NULL) {
        self->object.Reset(isolate, object);
    }
    return self;
}

void js_object_weak_callback(const WeakCallbackInfo<js_object> &info) {
    js_object *self = info.GetParameter();
    self->object.Reset();
    Py_XDECREF(self);
}

js_object *js_object_weak_new(Local<Object> object, Local<Context> context) {
    IN_V8;
    Context::Scope cs(context);
    js_object *self;
    if (object->IsPromise()) {
        self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
    } else if (object->IsCallable()) {
        self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
    } else if (object->IsTypedArray()) {
        self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
    } else {
        self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
    }

    if (self != NULL) {
        self->object.Reset(isolate, object);
        //self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kFinalizer);
        self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kParameter);
    }
    return self;
}

PyObject *js_object_getattro(js_object *self, PyObject *name) {

    if (PyObject_GenericHasAttr((PyObject *) self, name)) {
        return PyObject_GenericGetAttr((PyObject *) self, name);
    }
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
    if (Py_TYPE(value) == &js_function_type) {
        js_function *func = (js_function *) value;

        func->js_this.Reset(isolate, object);
    }
    return value;
}

int js_object_setattro(js_object *self, PyObject *name, PyObject *value) {
    if (PyObject_GenericHasAttr((PyObject *) self, name)) {
        return PyObject_GenericSetAttr((PyObject *) self, name, value);
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

PyObject *js_object_getiter(js_object *self) {
    PyObject *props = js_object_dir(self);
    PyErr_PROPAGATE(props);
    PyObject *iter = PySeqIter_New(props);
    Py_XDECREF(props);
    return iter;
}

PyObject *js_object_dir(js_object *self) {
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
        PyList_SET_ITEM(py_properties, i, py_property);
    }

    if (!context_cleanup_timeout(context)) return NULL;

    return py_properties;
}

PyObject* js_object_eq(js_object* self, PyObject* other) {
    if (!PyObject_TypeCheck(other, &js_object_type)) {
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

PyObject *js_object_repr(js_object *self) {
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    Local<String> v8_str;
    MaybeLocal<String> maybe_v8_str = object->ToString(context);
    if (!maybe_v8_str.ToLocal(&v8_str)) {
        return PyUnicode_FromString("[error toString]");
    }
    v8::String::Utf8Value str(isolate, v8_str);
    PyObject* repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;
    return repr;
}

PyObject* js_promise_repr(js_promise* self) {
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    v8::String::Utf8Value str(isolate, object->ToString(context).ToLocalChecked());
    PyObject* repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;
    return repr;
}

PyObject* js_typedarray_repr(js_typedarray* self) {
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    v8::String::Utf8Value str(isolate, object->ToString(context).ToLocalChecked());
    PyObject* text = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;

    PyObject* arry_type = NULL;
    if (object->IsUint32Array()) 
        arry_type = PyUnicode_FromString("Uint32Array[%s]");
    else if (object->IsUint16Array())
        arry_type = PyUnicode_FromString("Uint16Array[%s]");
    else if (object->IsUint8Array())
        arry_type = PyUnicode_FromString("Uint8Array[%s]");
    else if (object->IsInt32Array()) 
        arry_type = PyUnicode_FromString("Int32Array[%s]");
    else if (object->IsInt16Array())
        arry_type = PyUnicode_FromString("Int16Array[%s]");
    else if (object->IsInt8Array())
        arry_type = PyUnicode_FromString("Int8Array[%s]");
    else if (object->IsFloat64Array())
        arry_type = PyUnicode_FromString("Float64Array[%s]");
    else if (object->IsFloat32Array())
        arry_type = PyUnicode_FromString("Float32Array[%s]");
    else if (object->IsBigInt64Array())
        arry_type = PyUnicode_FromString("BigInt64Array[%s]");
    else if (object->IsBigUint64Array())
        arry_type = PyUnicode_FromString("BigUint64Array[%s]");
    else if (object->IsUint8ClampedArray())
        arry_type = PyUnicode_FromString("Uint8ClampedArray[%s]");
    else {
        arry_type = PyUnicode_FromString("UnKnownTypedArray[%s]");
    }
    PyObject* repr = PyUnicode_Format(arry_type, text);
    Py_XDECREF(text);
    Py_XDECREF(arry_type);
    return repr;
}

void js_object_dealloc(js_object *self) {
    self->object.Reset();
    Py_TYPE(self)->tp_free((PyObject *) self);
}

void js_promise_dealloc(js_promise *self) {
    js_object_dealloc((js_object *) self);
}

void js_typedarray_dealloc(js_typedarray* self) {
    self->object.Reset();
    js_object_dealloc((js_object*)self);
}
