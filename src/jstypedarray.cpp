#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "jstypedarray.h"
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

PyTypeObject* js_typedarray_type = NULL;

static PyType_Slot js_typedarray_slots[] = {
    {Py_tp_dealloc, (void*)js_typedarray_dealloc},
    {Py_tp_new, (void*)js_typearray_new},
    {Py_tp_repr, (void*)js_typedarray_repr},
    {Py_tp_doc, "v8 TypedArray"},
    {0, NULL} };

static PyType_Spec js_typedarray_spec = {
    "_pyv8.TypedArray",
    sizeof(js_typedarray),
    0,
    Py_TPFLAGS_DEFAULT,
    js_typedarray_slots
};


int 
js_typedarray_type_init()
{
    // Create a base class spec
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    PyTuple_SetItem(bases, 0, (PyObject*)js_object_type);
    Py_INCREF(js_object_type); // PyTuple_SetItem steals a reference

    js_typedarray_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_typedarray_spec, bases);
    Py_DECREF(bases);

    //js_typedarray_type = (PyTypeObject*)PyType_FromSpec(&js_typedarray_spec);
    if (js_typedarray_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject * 
js_typearray_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    IN_V8;

    PyObject* data = NULL;
    PyObject* typed = NULL;
    static const char* keywords[] = {"data", "typed", NULL };
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "OO", (char**)keywords,&data, &typed) < 0) {
        return NULL;
    }

    if (!(PyList_Check(data) || PyObject_TypeCheck(data, js_object_type)) || !PyLong_Check(typed)) {
        return NULL;
    }

    IN_CONTEXT(isolate->GetCurrentContext());
    //js_object* self = (js_object*)type->tp_alloc(type, 0);
    js_typedarray* self = (js_typedarray*)PyType_GenericAlloc(type, 0);


    v8::Local<v8::ArrayBuffer> array_buffer;

    if (PyObject_TypeCheck(data, js_object_type)) {
        js_object* py_value = (js_object*)data;
        array_buffer = py_value->object.Get(isolate).As<ArrayBuffer>();
        auto ary = v8::Uint8Array::New(array_buffer, 0, array_buffer->ByteLength());
        self->base.object.Reset(isolate, ary);
        return (PyObject*)self;
    }

    int length = PyList_Size(data);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
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
        self->base.object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Float32Array) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(float));
        auto ary = v8::Float32Array::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            ary->Set(isolate->GetCurrentContext(), i, v8::Number::New(isolate, PyFloat_AsDouble(val))).Check();
        }
        self->base.object.Reset(isolate, ary);
    }
    else if (array_type == ArrayType::Uint8ClampedArray) {
        array_buffer = v8::ArrayBuffer::New(isolate, length * sizeof(uint8_t));
        auto ary = v8::Uint8ClampedArray::New(array_buffer, 0, length);
        for (int i = 0; i < length; i++) {
            PyObject* val = PyList_GetItem(data, i);
            ary->Set(isolate->GetCurrentContext(), i, v8::Uint32::New(isolate, PyLong_AsLong(val))).Check();
        }
        self->base.object.Reset(isolate, ary);
    }else{
            return NULL;
    }
    
    return (PyObject*)self;
}

PyObject * 
js_typedarray_repr(js_typedarray* self)
{
    IN_V8;
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    v8::String::Utf8Value str(isolate, object->ToString(context).ToLocalChecked());
    PyObject* text = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;

    PyObject* arry_type = NULL;
    if (object->IsUint32Array()) 
        arry_type = PyUnicode_FromString("Uint32Array([%s])");
    else if (object->IsUint16Array())
        arry_type = PyUnicode_FromString("Uint16Array([%s])");
    else if (object->IsUint8Array())
        arry_type = PyUnicode_FromString("Uint8Array([%s])");
    else if (object->IsInt32Array()) 
        arry_type = PyUnicode_FromString("Int32Array([%s])");
    else if (object->IsInt16Array())
        arry_type = PyUnicode_FromString("Int16Array([%s])");
    else if (object->IsInt8Array())
        arry_type = PyUnicode_FromString("Int8Array([%s])");
    else if (object->IsFloat64Array())
        arry_type = PyUnicode_FromString("Float64Array([%s])");
    else if (object->IsFloat32Array())
        arry_type = PyUnicode_FromString("Float32Array([%s])");
    else if (object->IsBigInt64Array())
        arry_type = PyUnicode_FromString("BigInt64Array([%s])");
    else if (object->IsBigUint64Array())
        arry_type = PyUnicode_FromString("BigUint64Array([%s])");
    else if (object->IsUint8ClampedArray())
        arry_type = PyUnicode_FromString("Uint8ClampedArray([%s])");
    else {
        arry_type = PyUnicode_FromString("UnKnownTypedArray([%s])");
    }
    PyObject* repr = PyUnicode_Format(arry_type, text);
    Py_XDECREF(text);
    Py_XDECREF(arry_type);
    return repr;
}

void 
js_typedarray_dealloc(js_typedarray* self)
{
    js_object_dealloc((js_object*)self);
}
