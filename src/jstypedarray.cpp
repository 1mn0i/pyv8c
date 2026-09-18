#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "jstypedarray.h"
#include "context.h"

#include <limits>

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
    {Py_tp_doc, (void*)"v8 TypedArray"},
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

static bool
parse_array_type(PyObject* value, ArrayType* array_type)
{
    if (!PyLong_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "typed must be an ArrayType integer");
        return false;
    }
    long raw_type = PyLong_AsLong(value);
    if (PyErr_Occurred())
        return false;
    if (raw_type < static_cast<long>(ArrayType::Uint32Array) ||
        raw_type > static_cast<long>(ArrayType::Uint8ClampedArray)) {
        PyErr_SetString(PyExc_ValueError, "unsupported typed array type");
        return false;
    }
    *array_type = static_cast<ArrayType>(raw_type);
    return true;
}

static size_t
array_type_item_size(ArrayType array_type)
{
    switch (array_type) {
    case ArrayType::Uint32Array:
    case ArrayType::Int32Array:
    case ArrayType::Float32Array:
        return sizeof(uint32_t);
    case ArrayType::Uint16Array:
    case ArrayType::Int16Array:
        return sizeof(uint16_t);
    case ArrayType::Uint8Array:
    case ArrayType::Int8Array:
    case ArrayType::Uint8ClampedArray:
        return sizeof(uint8_t);
    case ArrayType::Float64Array:
        return sizeof(double);
    }
    return 0;
}

static Local<Object>
typed_array_from_buffer(Local<ArrayBuffer> array_buffer,
                       size_t byte_offset,
                       size_t byte_length,
                       ArrayType array_type)
{
    size_t item_size = array_type_item_size(array_type);
    if (item_size == 0 || byte_offset % item_size != 0 ||
        byte_length % item_size != 0) {
        PyErr_SetString(PyExc_ValueError,
                        "ArrayBuffer byte range is not aligned for the requested typed array");
        return Local<Object>();
    }

    size_t length = byte_length / item_size;
    switch (array_type) {
    case ArrayType::Uint32Array:
        return Uint32Array::New(array_buffer, byte_offset, length);
    case ArrayType::Uint16Array:
        return Uint16Array::New(array_buffer, byte_offset, length);
    case ArrayType::Uint8Array:
        return Uint8Array::New(array_buffer, byte_offset, length);
    case ArrayType::Int32Array:
        return Int32Array::New(array_buffer, byte_offset, length);
    case ArrayType::Int16Array:
        return Int16Array::New(array_buffer, byte_offset, length);
    case ArrayType::Int8Array:
        return Int8Array::New(array_buffer, byte_offset, length);
    case ArrayType::Float64Array:
        return Float64Array::New(array_buffer, byte_offset, length);
    case ArrayType::Float32Array:
        return Float32Array::New(array_buffer, byte_offset, length);
    case ArrayType::Uint8ClampedArray:
        return Uint8ClampedArray::New(array_buffer, byte_offset, length);
    }
    PyErr_SetString(PyExc_ValueError, "unsupported typed array type");
    return Local<Object>();
}

static Local<Object>
typed_array_from_list(Isolate* isolate, Local<Context> context,
                      PyObject* data, ArrayType array_type)
{
    Py_ssize_t py_length = PyList_Size(data);
    if (py_length < 0)
        return Local<Object>();
    if (static_cast<size_t>(py_length) >
        static_cast<size_t>(std::numeric_limits<uint32_t>::max())) {
        PyErr_SetString(PyExc_OverflowError, "typed array is too large");
        return Local<Object>();
    }

    size_t item_size = array_type_item_size(array_type);
    size_t length = static_cast<size_t>(py_length);
    if (item_size == 0 ||
        length > std::numeric_limits<size_t>::max() / item_size) {
        PyErr_SetString(PyExc_OverflowError, "typed array allocation is too large");
        return Local<Object>();
    }

    Local<ArrayBuffer> array_buffer = ArrayBuffer::New(
        isolate, length * item_size);
    if (array_buffer.IsEmpty()) {
        PyErr_NoMemory();
        return Local<Object>();
    }

    Local<Object> typed_array;
    switch (array_type) {
    case ArrayType::Uint32Array:
        typed_array = Uint32Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Uint16Array:
        typed_array = Uint16Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Uint8Array:
        typed_array = Uint8Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Int32Array:
        typed_array = Int32Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Int16Array:
        typed_array = Int16Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Int8Array:
        typed_array = Int8Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Float64Array:
        typed_array = Float64Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Float32Array:
        typed_array = Float32Array::New(array_buffer, 0, length);
        break;
    case ArrayType::Uint8ClampedArray:
        typed_array = Uint8ClampedArray::New(array_buffer, 0, length);
        break;
    }
    if (typed_array.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 typed array");
        return Local<Object>();
    }

    bool is_float_array = array_type == ArrayType::Float32Array ||
                          array_type == ArrayType::Float64Array;
    for (Py_ssize_t i = 0; i < py_length; ++i) {
        PyObject* item = PyList_GetItem(data, i);
        if (item == NULL)
            return Local<Object>();
        if (is_float_array) {
            if (!PyFloat_Check(item) && !PyLong_Check(item)) {
                PyErr_Format(PyExc_TypeError,
                             "typed array item %zd must be a number", i);
                return Local<Object>();
            }
        }
        else if (!PyLong_Check(item)) {
            PyErr_Format(PyExc_TypeError,
                         "typed array item %zd must be an integer", i);
            return Local<Object>();
        }

        double number = PyFloat_AsDouble(item);
        if (PyErr_Occurred())
            return Local<Object>();
        bool set_worked;
        if (!typed_array->Set(
                context, static_cast<uint32_t>(i),
                Number::New(isolate, number)).To(&set_worked) ||
            !set_worked) {
            PyErr_Format(PyExc_RuntimeError,
                         "failed to set typed array item %zd", i);
            return Local<Object>();
        }
    }
    return typed_array;
}

PyObject * 
js_typearray_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    if (!runtime_manager.require_initialized())
        return NULL;

    PyObject* data = NULL;
    PyObject* typed = NULL;
    static const char* keywords[] = {"data", "typed", NULL };
    if (!PyArg_ParseTupleAndKeywords(
            args, kwargs, "OO", (char**)keywords, &data, &typed))
        return NULL;

    if (!PyList_Check(data) && !PyObject_TypeCheck(data, js_object_type)) {
        PyErr_SetString(PyExc_TypeError,
                        "data must be a list, ArrayBuffer, or ArrayBufferView");
        return NULL;
    }
    ArrayType array_type;
    if (!parse_array_type(typed, &array_type))
        return NULL;

    IsolateState* isolate_state = current_isolate_state();
    if (isolate_state == nullptr)
        isolate_state = runtime_manager.default_isolate();
    IN_ISOLATE(isolate_state);
    Local<Context> current_context = isolate->GetCurrentContext();
    if (current_context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError,
                        "JSTypeArray requires an active V8 Context");
        return NULL;
    }
    IN_CONTEXT(current_context);

    Local<Object> typed_array;
    if (PyObject_TypeCheck(data, js_object_type)) {
        js_object* py_value = (js_object*)data;
        if (py_value->isolate_state != isolate_state) {
            PyErr_SetString(PyExc_RuntimeError,
                            "JavaScript value belongs to another isolate");
            return NULL;
        }
        Local<Object> source = py_value->object.Get(isolate);
        Local<ArrayBuffer> array_buffer;
        size_t byte_offset = 0;
        size_t byte_length = 0;
        if (source->IsArrayBuffer()) {
            array_buffer = source.As<ArrayBuffer>();
            byte_length = array_buffer->ByteLength();
        }
        else if (source->IsArrayBufferView()) {
            Local<ArrayBufferView> view = source.As<ArrayBufferView>();
            array_buffer = view->Buffer();
            byte_offset = view->ByteOffset();
            byte_length = view->ByteLength();
        }
        else {
            PyErr_SetString(PyExc_TypeError,
                            "JSObject data must wrap an ArrayBuffer or ArrayBufferView");
            return NULL;
        }
        typed_array = typed_array_from_buffer(
            array_buffer, byte_offset, byte_length, array_type);
    }
    else {
        typed_array = typed_array_from_list(
            isolate, context, data, array_type);
        if (typed_array.IsEmpty())
            return NULL;
    }

    if (typed_array.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 typed array");
        return NULL;
    }
    js_typedarray* self = (js_typedarray*)PyType_GenericAlloc(type, 0);
    if (self == NULL)
        return NULL;
    js_object_set_persistent((js_object*)self, typed_array, context, true);
    return (PyObject*)self;
}

PyObject * 
js_typedarray_repr(js_typedarray* self)
{
    if (!js_object_require_active(&self->base))
        return NULL;
    IN_ISOLATE(self->base.isolate_state);
    if (!js_object_require_active(&self->base)) return nullptr;
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    Local<String> js_text;
    if (!object->ToString(context).ToLocal(&js_text)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to stringify JavaScript typed array");
        return NULL;
    }
    v8::String::Utf8Value str(isolate, js_text);
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
