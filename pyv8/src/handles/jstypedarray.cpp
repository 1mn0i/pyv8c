#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "runtime.h"
#include "crossing.h"
#include <v8.h>

#include "convert.h"
#include "error_family.h"
#include "jsobject.h"
#include "jstypedarray.h"
#include "context.h"

#include <cstdint>
#include <limits>

using namespace v8;


//: Every refusal below names a kind of the family; nothing here holds a type.


enum class ArrayType {
    Uint32Array = 1,
    Uint16Array,
    Uint8Array,
    Int32Array,
    Int16Array,
    Int8Array,
    Float64Array,
    Float32Array,
    Uint8ClampedArray,
    // Appended rather than inserted: the numbers a host already passes keep their meaning.
    BigInt64Array,
    BigUint64Array
};

PyTypeObject* js_typedarray_type = NULL;

//: ``bytes(view)`` answers the range of the buffer the view looks at, rather than the
//: property names a plain JavaScript object iterates.
static PyMethodDef js_typedarray_methods[] = {
    {"__bytes__", (PyCFunction)PythonBoundary<js_object_bytes>::call, METH_NOARGS,
     "The bytes this view holds"},
    {NULL, NULL, 0, NULL} };

static PyType_Slot js_typedarray_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_typedarray_dealloc>::call},
    {Py_tp_new, (void*)PythonBoundary<js_typearray_new>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_typedarray_repr>::call},
    {Py_tp_methods, (void*)js_typedarray_methods},
    {Py_tp_doc, (void*)"v8 TypedArray"},
    {0, NULL} };

static PyType_Spec js_typedarray_spec = {
    "pyv8c.pyv8.JSTypeArray",
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
    Py_INCREF((PyObject*)js_object_type); // PyTuple_SetItem steals a reference

    js_typedarray_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_typedarray_spec, bases);
    Py_DECREF((PyObject*)bases);

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
        error_family::raise(error_family::Kind::TypeError, "%s", "typed must be an ArrayType integer");
        return false;
    }
    long raw_type = PyLong_AsLong(value);
    if (PyErr_Occurred())
        return false;
    if (raw_type < static_cast<long>(ArrayType::Uint32Array) ||
        raw_type > static_cast<long>(ArrayType::BigUint64Array)) {
        error_family::raise(error_family::Kind::RangeError, "%s", "unsupported typed array type");
        return false;
    }
    *array_type = static_cast<ArrayType>(raw_type);
    return true;
}

//: The name a realm answers for this kind, which is what V8's wording uses.
static const char*
array_type_name(ArrayType array_type)
{
    switch (array_type) {
    case ArrayType::Uint32Array: return "Uint32Array";
    case ArrayType::Uint16Array: return "Uint16Array";
    case ArrayType::Uint8Array: return "Uint8Array";
    case ArrayType::Int32Array: return "Int32Array";
    case ArrayType::Int16Array: return "Int16Array";
    case ArrayType::Int8Array: return "Int8Array";
    case ArrayType::Float64Array: return "Float64Array";
    case ArrayType::Float32Array: return "Float32Array";
    case ArrayType::Uint8ClampedArray: return "Uint8ClampedArray";
    case ArrayType::BigInt64Array: return "BigInt64Array";
    case ArrayType::BigUint64Array: return "BigUint64Array";
    }
    return "TypedArray";
}

//: Whether the bytes of this kind are BigInts, which a realm converts with ToBigInt rather
//: than ToNumber.
static bool
array_type_is_bigint(ArrayType array_type)
{
    return array_type == ArrayType::BigInt64Array ||
           array_type == ArrayType::BigUint64Array;
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
    case ArrayType::BigInt64Array:
    case ArrayType::BigUint64Array:
        return sizeof(uint64_t);
    }
    return 0;
}

//: The typed array V8 builds for this kind over *buffer*.  Chromium chooses the overload
//: from the backing store's sharedness the same way (dom_typed_array.cc:26-32), because
//: the two entry points refuse each other's store (api.cc:9275-9288, 9628-9641).
#define PYV8_TYPED_ARRAY_ON_BUFFER(Kind)                                       \
    do {                                                                       \
        if (shared)                                                            \
            return Kind::New(buffer.As<SharedArrayBuffer>(), byte_offset,      \
                             length);                                          \
        return Kind::New(buffer.As<ArrayBuffer>(), byte_offset, length);       \
    } while (0)

//: The view a realm builds for ``new <Kind>(buffer, byteOffset, byteLength)``.  The checks,
//: their order and their wording are V8's (typed-array-createtypedarray.tq:314-396 and
//: message-template.h:425/435/437/107-108); Blink keeps the same rules in
//: DOMTypedArray::VerifySubRange (dom_typed_array.h:123-138).
static Local<Object>
typed_array_from_buffer(Local<Value> buffer, size_t byte_offset, size_t byte_length,
                        ArrayType array_type)
{
    const size_t item_size = array_type_item_size(array_type);
    const char* type_name = array_type_name(array_type);
    if (item_size == 0) {
        error_family::raise(error_family::Kind::TypeError, "%s", "unsupported typed array type");
        return Local<Object>();
    }
    if (byte_offset % item_size != 0) {
        // "start offset of Uint8Array should be a multiple of 2"
        error_family::raise(error_family::Kind::RangeError,
                     "start offset of %s should be a multiple of %zu", type_name,
                     item_size);
        return Local<Object>();
    }
    if (byte_length % item_size != 0) {
        error_family::raise(error_family::Kind::RangeError,
                     "byte length of %s should be a multiple of %zu", type_name,
                     item_size);
        return Local<Object>();
    }

    const bool shared = buffer->IsSharedArrayBuffer();
    const size_t capacity = shared
        ? static_cast<size_t>(buffer.As<SharedArrayBuffer>()->ByteLength())
        : static_cast<size_t>(buffer.As<ArrayBuffer>()->ByteLength());
    if (!shared && buffer.As<ArrayBuffer>()->WasDetached()) {
        // "Cannot perform Construct on a detached ArrayBuffer"
        error_family::raise(error_family::Kind::TypeError, "%s",
                        "Cannot perform Construct on a detached ArrayBuffer");
        return Local<Object>();
    }
    if (byte_offset > capacity) {
        error_family::raise(error_family::Kind::RangeError,
                     "Start offset %zu is outside the bounds of the buffer", byte_offset);
        return Local<Object>();
    }
    if (byte_length > capacity - byte_offset) {
        error_family::raise(error_family::Kind::RangeError,
                     "Invalid typed array length: %zu", byte_length / item_size);
        return Local<Object>();
    }

    const size_t length = byte_length / item_size;
    switch (array_type) {
    case ArrayType::Uint32Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Uint32Array);
    case ArrayType::Uint16Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Uint16Array);
    case ArrayType::Uint8Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Uint8Array);
    case ArrayType::Int32Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Int32Array);
    case ArrayType::Int16Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Int16Array);
    case ArrayType::Int8Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Int8Array);
    case ArrayType::Float64Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Float64Array);
    case ArrayType::Float32Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(Float32Array);
    case ArrayType::Uint8ClampedArray:
        PYV8_TYPED_ARRAY_ON_BUFFER(Uint8ClampedArray);
    case ArrayType::BigInt64Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(BigInt64Array);
    case ArrayType::BigUint64Array:
        PYV8_TYPED_ARRAY_ON_BUFFER(BigUint64Array);
    }
    error_family::raise(error_family::Kind::TypeError, "%s", "unsupported typed array type");
    return Local<Object>();
}

#undef PYV8_TYPED_ARRAY_ON_BUFFER

//: One of the two byte counts a caller may pass alongside an ArrayBuffer.  V8 reads them
//: with ToIndex, so a negative or unrepresentable one is the RangeError a realm would
//: raise; anything that is not an integer is the TypeError a realm would raise.
static bool
read_byte_count(PyObject* value, bool is_offset, size_t* out)
{
    if (!PyLong_Check(value)) {
        error_family::raise(error_family::Kind::TypeError, "%s must be an integer",
                     is_offset ? "byte_offset" : "byte_length");
        return false;
    }
    const long long raw = PyLong_AsLongLong(value);
    if (raw == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        error_family::raise(error_family::Kind::RangeError,
                     is_offset ? "Start offset %R is outside the bounds of the buffer"
                               : "Invalid typed array length: %R",
                     value);
        return false;
    }
    if (raw < 0) {
        error_family::raise(error_family::Kind::RangeError,
                     is_offset ? "Start offset %R is outside the bounds of the buffer"
                               : "Invalid typed array length: %R",
                     value);
        return false;
    }
    *out = static_cast<size_t>(raw);
    return true;
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
    case ArrayType::BigInt64Array:
        typed_array = BigInt64Array::New(array_buffer, 0, length);
        break;
    case ArrayType::BigUint64Array:
        typed_array = BigUint64Array::New(array_buffer, 0, length);
        break;
    }
    if (typed_array.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 typed array");
        return Local<Object>();
    }

    if (array_type_is_bigint(array_type)) {
        // ToBigInt64/ToBigUint64: the low 64 bits, read as signed for the first one.  The
        // mask does the modulo, and C++20 defines the narrowing cast as two's complement.
        const bool signed_array = array_type == ArrayType::BigInt64Array;
        for (Py_ssize_t i = 0; i < py_length; ++i) {
            PyObject* item = PyList_GetItem(data, i);
            if (item == NULL)
                return Local<Object>();
            if (!PyLong_Check(item)) {
                error_family::raise(error_family::Kind::TypeError, "Cannot convert %R to a BigInt", item);
                return Local<Object>();
            }
            const unsigned long long raw = PyLong_AsUnsignedLongLongMask(item);
            if (PyErr_Occurred())
                return Local<Object>();
            Local<BigInt> value = signed_array
                ? BigInt::New(isolate, static_cast<int64_t>(raw))
                : BigInt::NewFromUnsigned(isolate, raw);
            bool set_worked;
            if (!typed_array->Set(context, static_cast<uint32_t>(i), value).To(&set_worked) ||
                !set_worked) {
                PyErr_Format(PyExc_RuntimeError,
                             "failed to set typed array item %zd", i);
                return Local<Object>();
            }
        }
        return typed_array;
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
    if (!runtime_manager.ensure_initialized())
        return NULL;

    PyObject* data = NULL;
    PyObject* typed = NULL;
    PyObject* byte_offset = NULL;
    PyObject* byte_length = NULL;
    static const char* keywords[] = {"data", "typed", "byte_offset", "byte_length", NULL };
    if (!PyArg_ParseTupleAndKeywords(
            args, kwargs, "OO|OO", (char**)keywords, &data, &typed,
            &byte_offset, &byte_length))
        return NULL;

    if (!PyList_Check(data) && !PyObject_TypeCheck(data, js_object_type)) {
        error_family::raise(error_family::Kind::TypeError, "%s",
                        "data must be a list, ArrayBuffer, SharedArrayBuffer"
                        " or ArrayBufferView");
        return NULL;
    }
    ArrayType array_type;
    if (!parse_array_type(typed, &array_type))
        return NULL;

    IsolateState* isolate_state = select_isolate(IsolateSelection::ActiveOrDefault);
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
        if (!js_object_require_active(py_value)) return nullptr;
        if (py_value->isolate_state != isolate_state) {
            PyErr_SetString(PyExc_RuntimeError,
                            "JavaScript value belongs to another isolate");
            return NULL;
        }
        Local<Object> source = py_value->object.Get(isolate);
        Local<Value> buffer;
        size_t offset = 0;
        size_t length = 0;
        const bool is_view = source->IsArrayBufferView();
        if (source->IsArrayBuffer()) {
            buffer = source;
            length = static_cast<size_t>(source.As<ArrayBuffer>()->ByteLength());
        }
        else if (source->IsSharedArrayBuffer()) {
            buffer = source;
            length = static_cast<size_t>(
                source.As<SharedArrayBuffer>()->ByteLength());
        }
        else if (is_view) {
            Local<ArrayBufferView> view = source.As<ArrayBufferView>();
            buffer = view->Buffer();
            offset = view->ByteOffset();
            length = view->ByteLength();
        }
        else {
            error_family::raise(error_family::Kind::TypeError, "%s",
                            "JSObject data must wrap an ArrayBuffer, a SharedArrayBuffer"
                            " or an ArrayBufferView");
            return NULL;
        }
        // A range is a property of the buffer: V8's constructor draws the same line between
        // its buffer form and its iterable form, so a view is taken whole.
        if (is_view && (byte_offset != NULL || byte_length != NULL)) {
            error_family::raise(error_family::Kind::TypeError, "%s",
                            "byte_offset and byte_length apply to a buffer, not to a view");
            return NULL;
        }
        if (byte_offset != NULL && !read_byte_count(byte_offset, true, &offset))
            return NULL;
        if (byte_length != NULL && !read_byte_count(byte_length, false, &length))
            return NULL;
        typed_array = typed_array_from_buffer(buffer, offset, length, array_type);
    }
    else {
        if (byte_offset != NULL || byte_length != NULL) {
            error_family::raise(error_family::Kind::TypeError, "%s",
                            "byte_offset and byte_length apply to a buffer, not to a list");
            return NULL;
        }
        typed_array = typed_array_from_list(
            isolate, context, data, array_type);
        if (typed_array.IsEmpty())
            return NULL;
    }

    if (typed_array.IsEmpty()) {
        // The helpers above refuse an argument with the family error a realm would raise;
        // only an unexplained empty handle is the host's own failure.
        if (!PyErr_Occurred())
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
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
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
    Py_XDECREF((PyObject*)text);
    Py_XDECREF((PyObject*)arry_type);
    return repr;
}

void 
js_typedarray_dealloc(js_typedarray* self)
{
    js_object_dealloc((js_object*)self);
}
