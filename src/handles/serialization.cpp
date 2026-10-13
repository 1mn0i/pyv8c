// One realm's serializer/deserializer pair: what Chromium's structured clone is.
//
// ``UniversalGlobalScope::structuredClone`` serializes the value and deserializes the result
// (third_party/blink/renderer/core/frame/universal_global_scope.cc), and the same pair backs
// postMessage and IndexedDB.  The wire format is V8's own, so everything V8 knows -- objects,
// arrays, Map/Set/Date/RegExp, Error with name/message/cause/stack, ArrayBuffer and views,
// cycles through its object-reference table, transferred buffers -- behaves as it does in a
// browser by construction.  What V8 cannot know is the host: a Blob, a File, a DOMException.
// Those reach Python through the two callbacks, and the primitives they use are the
// ``SerializationWriter`` and ``SerializationReader`` below -- the same shape as Blink's
// ``V8ScriptValueSerializer::WriteDOMObject`` and its deserializer counterpart.

#include "serialization.h"

//: The order every source here keeps: the module header first, then V8.  V8's headers read
//: what ``pyv8.h`` sets up (the compat shims included), so the other way round they fail to
//: parse -- with errors pointing inside V8 rather than here.
#include "pyv8.h"
#include <v8.h>

#include <utility>
#include <vector>

#include "abi3.h"
#include "boundary.h"
#include "context.h"
#include "convert.h"
#include "crossing.h"
#include "execution.h"
#include "isolate_state.h"
#include "js_exception.h"
#include "jsobject.h"
#include "jsvalue.h"

namespace
{

typedef struct
{
    PyObject_HEAD
    v8::ValueSerializer* serializer;
    v8::Isolate* isolate;
    unsigned long thread_id;
} js_serialization_writer;

typedef struct
{
    PyObject_HEAD
    v8::ValueDeserializer* deserializer;
    v8::Isolate* isolate;
    unsigned long thread_id;
} js_serialization_reader;

// ---------------------------------------------------------------- writer

PyObject* js_serialization_writer_new(v8::ValueSerializer* serializer, v8::Isolate* isolate)
{
    js_serialization_writer* self =
        (js_serialization_writer*)PyType_GenericAlloc(serialization_writer_type, 0);
    PyErr_PROPAGATE(self);
    self->serializer = serializer;
    self->isolate = isolate;
    self->thread_id = PyThread_get_thread_ident();
    return (PyObject*)self;
}

void js_serialization_writer_dealloc(js_serialization_writer* self)
{
    TP_FREE_ABI3(serialization_writer_type, self);
}

bool writer_active(js_serialization_writer* self)
{
    if (self->serializer == nullptr || self->thread_id != PyThread_get_thread_ident())
    {
        PyErr_SetString(PyExc_RuntimeError,
            "SerializationWriter is only valid during its callback on the creating thread");
        return false;
    }
    return true;
}

PyObject* js_serialization_writer_write_tag(js_serialization_writer* self, PyObject* tag)
{
    if (!writer_active(self)) return nullptr;
    long value = PyLong_AsLong(tag);
    if (value == -1 && PyErr_Occurred())
        return nullptr;
    if (value < 0 || value > 255)
    {
        PyErr_SetString(PyExc_ValueError, "a serialization tag must be between 0 and 255");
        return nullptr;
    }
    uint8_t byte = (uint8_t)value;
    self->serializer->WriteRawBytes(&byte, 1);
    Py_RETURN_NONE;
}

PyObject* js_serialization_writer_write_uint32(js_serialization_writer* self, PyObject* number)
{
    if (!writer_active(self)) return nullptr;
    unsigned long value = PyLong_AsUnsignedLong(number);
    if (value == (unsigned long)-1 && PyErr_Occurred())
        return nullptr;
    self->serializer->WriteUint32((uint32_t)value);
    Py_RETURN_NONE;
}

PyObject* js_serialization_writer_write_uint64(js_serialization_writer* self, PyObject* number)
{
    if (!writer_active(self)) return nullptr;
    unsigned long long value = PyLong_AsUnsignedLongLong(number);
    if (value == (unsigned long long)-1 && PyErr_Occurred())
        return nullptr;
    self->serializer->WriteUint64((uint64_t)value);
    Py_RETURN_NONE;
}

PyObject* js_serialization_writer_write_double(js_serialization_writer* self, PyObject* number)
{
    if (!writer_active(self)) return nullptr;
    double value = PyFloat_AsDouble(number);
    if (value == -1.0 && PyErr_Occurred())
        return nullptr;
    self->serializer->WriteDouble(value);
    Py_RETURN_NONE;
}

PyObject* js_serialization_writer_write_raw_bytes(js_serialization_writer* self, PyObject* data)
{
    if (!writer_active(self)) return nullptr;
    Py_buffer view;
    if (PyObject_GetBuffer(data, &view, PyBUF_SIMPLE) < 0)
        return nullptr;
    self->serializer->WriteRawBytes(view.buf, (size_t)view.len);
    PyBuffer_Release(&view);
    Py_RETURN_NONE;
}

PyMethodDef js_serialization_writer_methods[] = {
    {"write_tag", (PyCFunction)PythonBoundary<js_serialization_writer_write_tag>::call,
     METH_O, "write one tag byte"},
    {"write_uint32", (PyCFunction)PythonBoundary<js_serialization_writer_write_uint32>::call,
     METH_O, "write a uint32"},
    {"write_uint64", (PyCFunction)PythonBoundary<js_serialization_writer_write_uint64>::call,
     METH_O, "write a uint64"},
    {"write_double", (PyCFunction)PythonBoundary<js_serialization_writer_write_double>::call,
     METH_O, "write a double"},
    {"write_raw_bytes", (PyCFunction)PythonBoundary<js_serialization_writer_write_raw_bytes>::call,
     METH_O, "write raw bytes"},
    {NULL, NULL}};

PyType_Slot js_serialization_writer_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_serialization_writer_dealloc>::call},
    {Py_tp_methods, js_serialization_writer_methods},
    {Py_tp_doc, (void*)"the bytes a host object is written as (valid during the callback)"},
    {0, NULL}};

PyType_Spec js_serialization_writer_spec = {
    "pyv8.SerializationWriter",
    sizeof(js_serialization_writer),
    0,
    Py_TPFLAGS_DEFAULT,
    js_serialization_writer_slots};

// ---------------------------------------------------------------- reader

PyObject* js_serialization_reader_new(v8::ValueDeserializer* deserializer, v8::Isolate* isolate)
{
    js_serialization_reader* self =
        (js_serialization_reader*)PyType_GenericAlloc(serialization_reader_type, 0);
    PyErr_PROPAGATE(self);
    self->deserializer = deserializer;
    self->isolate = isolate;
    self->thread_id = PyThread_get_thread_ident();
    return (PyObject*)self;
}

void js_serialization_reader_dealloc(js_serialization_reader* self)
{
    TP_FREE_ABI3(serialization_reader_type, self);
}

bool reader_active(js_serialization_reader* self)
{
    if (self->deserializer == nullptr || self->thread_id != PyThread_get_thread_ident())
    {
        PyErr_SetString(PyExc_RuntimeError,
            "SerializationReader is only valid during its callback on the creating thread");
        return false;
    }
    return true;
}

PyObject* js_serialization_reader_read_tag(js_serialization_reader* self, PyObject* Py_UNUSED(ignored))
{
    if (!reader_active(self)) return nullptr;
    const void* data = nullptr;
    if (!self->deserializer->ReadRawBytes(1, &data))
    {
        PyErr_SetString(PyExc_ValueError, "the serialized stream ended");
        return nullptr;
    }
    return PyLong_FromLong((long)*(const uint8_t*)data);
}

PyObject* js_serialization_reader_read_uint32(js_serialization_reader* self, PyObject* Py_UNUSED(ignored))
{
    if (!reader_active(self)) return nullptr;
    uint32_t value = 0;
    if (!self->deserializer->ReadUint32(&value))
    {
        PyErr_SetString(PyExc_ValueError, "the serialized stream ended");
        return nullptr;
    }
    return PyLong_FromUnsignedLong(value);
}

PyObject* js_serialization_reader_read_uint64(js_serialization_reader* self, PyObject* Py_UNUSED(ignored))
{
    if (!reader_active(self)) return nullptr;
    uint64_t value = 0;
    if (!self->deserializer->ReadUint64(&value))
    {
        PyErr_SetString(PyExc_ValueError, "the serialized stream ended");
        return nullptr;
    }
    return PyLong_FromUnsignedLongLong(value);
}

PyObject* js_serialization_reader_read_double(js_serialization_reader* self, PyObject* Py_UNUSED(ignored))
{
    if (!reader_active(self)) return nullptr;
    double value = 0;
    if (!self->deserializer->ReadDouble(&value))
    {
        PyErr_SetString(PyExc_ValueError, "the serialized stream ended");
        return nullptr;
    }
    return PyFloat_FromDouble(value);
}

PyObject* js_serialization_reader_read_raw_bytes(js_serialization_reader* self, PyObject* count)
{
    if (!reader_active(self)) return nullptr;
    Py_ssize_t length = PyLong_AsSsize_t(count);
    if (length < 0)
    {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_ValueError, "a byte count cannot be negative");
        return nullptr;
    }
    const void* data = nullptr;
    if (!self->deserializer->ReadRawBytes((size_t)length, &data))
    {
        PyErr_SetString(PyExc_ValueError, "the serialized stream ended");
        return nullptr;
    }
    return PyBytes_FromStringAndSize((const char*)data, length);
}

PyMethodDef js_serialization_reader_methods[] = {
    {"read_tag", (PyCFunction)PythonBoundary<js_serialization_reader_read_tag>::call,
     METH_NOARGS, "read one tag byte"},
    {"read_uint32", (PyCFunction)PythonBoundary<js_serialization_reader_read_uint32>::call,
     METH_NOARGS, "read a uint32"},
    {"read_uint64", (PyCFunction)PythonBoundary<js_serialization_reader_read_uint64>::call,
     METH_NOARGS, "read a uint64"},
    {"read_double", (PyCFunction)PythonBoundary<js_serialization_reader_read_double>::call,
     METH_NOARGS, "read a double"},
    {"read_raw_bytes", (PyCFunction)PythonBoundary<js_serialization_reader_read_raw_bytes>::call,
     METH_O, "read raw bytes"},
    {NULL, NULL}};

PyType_Slot js_serialization_reader_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_serialization_reader_dealloc>::call},
    {Py_tp_methods, js_serialization_reader_methods},
    {Py_tp_doc, (void*)"the bytes a host object is read from (valid during the callback)"},
    {0, NULL}};

PyType_Spec js_serialization_reader_spec = {
    "pyv8.SerializationReader",
    sizeof(js_serialization_reader),
    0,
    Py_TPFLAGS_DEFAULT,
    js_serialization_reader_slots};

// ---------------------------------------------------------------- delegates

//: The Python callbacks one call carries.  A failure is never swallowed: a Python exception
//: stays set for the caller of ``serialize``/``deserialize``, and V8's own failure is
//: propagated by the caller through ``PY_PROPAGATE_JS``.
class ValueWriterDelegate : public v8::ValueSerializer::Delegate
{
public:
    ValueWriterDelegate(v8::Isolate* isolate, v8::Local<v8::Context> context, PyObject* host_writer,
                        PyObject* host_check)
        : isolate_(isolate), context_(context),
          //: ``None`` is how Python spells "no callback", and the two mean the same thing here.
          host_writer_(host_writer != nullptr && host_writer != Py_None ? PyV8_NewRef(host_writer)
                                                                        : nullptr),
          host_check_(host_check != nullptr && host_check != Py_None ? PyV8_NewRef(host_check)
                                                                     : nullptr) {}

    ~ValueWriterDelegate() override
    {
        Py_XDECREF(host_writer_);
        Py_XDECREF(host_check_);
    }

    void set_serializer(v8::ValueSerializer* serializer) { serializer_ = serializer; }

    //: V8 requires this override: it is where a DataCloneError belongs.  The message is V8's
    //: own ("... could not be cloned."), which is the wording Chrome shows; the host turns it
    //: into the page's DataCloneError at the call site (clone.py).
    void ThrowDataCloneError(v8::Local<v8::String> message) override
    {
        isolate_->ThrowException(v8::Exception::Error(message));
    }

    //: Which objects are the host's.  V8 asks this about every object before deciding whether
    //: to write its properties itself, so the answer is what makes a Blob or a DOMException
    //: reach the callbacks at all -- Blink's V8ScriptValueSerializer::IsHostObject answers the
    //: same question about its wrappables.
    bool HasCustomHostObject(v8::Isolate* isolate) override { return host_check_ != nullptr; }

    v8::Maybe<bool> IsHostObject(v8::Isolate* isolate, v8::Local<v8::Object> object) override
    {
        // V8 forbids JavaScript while traversing its wire format. Host callbacks
        // are embedder code and may legitimately call back into their realm.
        v8::Isolate::AllowJavascriptExecutionScope callbacks(isolate);
        if (host_check_ == nullptr)
            return v8::Just(false);
        js_object* wrapper = js_object_new(object, context_);
        if (wrapper == nullptr)
            return v8::Nothing<bool>();
        PyObject* answer = PyObject_CallFunctionObjArgs(host_check_, (PyObject*)wrapper, NULL);
        Py_DECREF((PyObject*)wrapper);
        if (answer == nullptr)
            return v8::Nothing<bool>();
        int mine = PyObject_IsTrue(answer);
        Py_DECREF(answer);
        if (mine < 0)
            return v8::Nothing<bool>();
        //: "No" is an answer, not a failure: V8 has to go on and write the object itself.
        return v8::Just(mine == 1);
    }

    v8::Maybe<bool> WriteHostObject(v8::Isolate* isolate, v8::Local<v8::Object> object) override
    {
        // V8 forbids JavaScript while traversing its wire format. Host callbacks
        // are embedder code and may legitimately call back into their realm.
        v8::Isolate::AllowJavascriptExecutionScope callbacks(isolate);
        if (host_writer_ == nullptr)
        {
            //: A host object whose clone cannot be written: V8's own refusal, thrown where the
            //: spec puts it.  V8 counts on the delegate to throw here, and a bare failure
            //: would leave the serializer without the exception it reports.
            isolate->ThrowException(v8::Exception::Error(
                v8::String::NewFromUtf8Literal(isolate, "An object could not be cloned.")));
            return v8::Nothing<bool>();
        }
        js_object* wrapper = js_object_new(object, context_);
        if (wrapper == nullptr)
            return v8::Nothing<bool>();
        PyObject* writer = js_serialization_writer_new(serializer_, isolate);
        if (writer == nullptr)
        {
            Py_DECREF((PyObject*)wrapper);
            return v8::Nothing<bool>();
        }
        PyObject* answer =
            PyObject_CallFunctionObjArgs(host_writer_, (PyObject*)wrapper, writer, NULL);
        ((js_serialization_writer*)writer)->serializer = nullptr;
        Py_DECREF((PyObject*)wrapper);
        Py_DECREF(writer);
        if (answer == nullptr)
            return v8::Nothing<bool>();
        int wrote = PyObject_IsTrue(answer);
        Py_DECREF(answer);
        if (wrote < 0)
            return v8::Nothing<bool>();
        if (wrote != 1)
        {
            //: Claimed as the host's and then not written: the refusal V8's delegate contract
            //: asks for, and what the page sees as a DataCloneError.
            isolate->ThrowException(v8::Exception::Error(
                v8::String::NewFromUtf8Literal(isolate, "An object could not be cloned.")));
            return v8::Nothing<bool>();
        }
        return v8::Just(true);
    }

private:
    v8::Isolate* isolate_;
    v8::Local<v8::Context> context_;
    PyObject* host_writer_;
    PyObject* host_check_;
    v8::ValueSerializer* serializer_ = nullptr;
};

class ValueReaderDelegate : public v8::ValueDeserializer::Delegate
{
public:
    ValueReaderDelegate(v8::Local<v8::Context> context, PyObject* host_reader)
        : context_(context), host_reader_(host_reader && host_reader != Py_None ? PyV8_NewRef(host_reader) : nullptr) {}

    ~ValueReaderDelegate() override { Py_XDECREF(host_reader_); }

    void set_deserializer(v8::ValueDeserializer* deserializer) { deserializer_ = deserializer; }

    v8::MaybeLocal<v8::Object> ReadHostObject(v8::Isolate* isolate) override
    {
        // V8 forbids JavaScript while traversing its wire format. Host callbacks
        // are embedder code and may legitimately call back into their realm.
        v8::Isolate::AllowJavascriptExecutionScope callbacks(isolate);
        if (host_reader_ == nullptr)
            return v8::MaybeLocal<v8::Object>();
        PyObject* reader = js_serialization_reader_new(deserializer_, isolate);
        if (reader == nullptr)
            return v8::MaybeLocal<v8::Object>();
        PyObject* answer = PyObject_CallFunctionObjArgs(host_reader_, reader, NULL);
        ((js_serialization_reader*)reader)->deserializer = nullptr;
        Py_DECREF(reader);
        if (answer == nullptr)
            return v8::MaybeLocal<v8::Object>();
        v8::Local<v8::Value> value = js_from_py(answer, context_);
        Py_DECREF(answer);
        if (value.IsEmpty())
            return v8::MaybeLocal<v8::Object>();
        v8::Local<v8::Object> object;
        if (!value->ToObject(context_).ToLocal(&object))
            return v8::MaybeLocal<v8::Object>();
        return object;
    }

private:
    v8::Local<v8::Context> context_;
    PyObject* host_reader_;
    v8::ValueDeserializer* deserializer_ = nullptr;
};

// ---------------------------------------------------------------- serializer

typedef struct
{
    PyObject_HEAD
    PyObject* py_context;
    PyObject* py_isolate;
} js_serializer;

//: The transfer list, applied before the value is written and detached once it has been: an
//: ArrayBuffer named here travels as a transfer (its bytes are not copied) and the sender's
//: buffer is left detached, which is what a browser leaves behind.
bool apply_transfer(v8::ValueSerializer& serializer, PyObject* listed, v8::Local<v8::Context> context,
                    std::vector<v8::Local<v8::ArrayBuffer>>* buffers)
{
    if (listed == nullptr || listed == Py_None)
        return true;
    Py_ssize_t count = PySequence_Size(listed);
    if (count < 0)
    {
        PyErr_Clear();
        PyErr_SetString(PyExc_TypeError, "transfer must be a sequence");
        return false;
    }
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyObject* entry = PySequence_GetItem(listed, index);
        if (entry == nullptr)
            return false;
        v8::Local<v8::Value> item = js_from_py(entry, context);
        Py_DECREF(entry);
        if (item.IsEmpty() || !item->IsArrayBuffer())
        {
            if (!PyErr_Occurred())
                PyErr_Format(PyExc_TypeError, "transfer entry %zd is not an ArrayBuffer", index);
            return false;
        }
        serializer.TransferArrayBuffer((uint32_t)index, item.As<v8::ArrayBuffer>());
        buffers->push_back(item.As<v8::ArrayBuffer>());
    }
    return true;
}

PyObject* js_serializer_serialize(js_serializer* self, PyObject* args, PyObject* kwargs)
{
    static const char* keywords[] = {"value", "transfer", "host_writer", "host_check", NULL};
    PyObject* py_value = nullptr;
    PyObject* py_transfer = nullptr;
    PyObject* py_writer = nullptr;
    PyObject* py_check = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOO", (char**)keywords, &py_value, &py_transfer,
                                     &py_writer, &py_check))
        return nullptr;

    ExecutionScope execution((context_c*)self->py_context, (PyObject*)self);
    if (!execution)
        return nullptr;
    v8::Isolate* isolate = execution.isolate();
    v8::Local<v8::Context> context = execution.context();
    JS_TRY

    v8::Local<v8::Value> value = js_from_py(py_value, context);
    if (value.IsEmpty())
        return nullptr;

    ValueWriterDelegate delegate(isolate, context, py_writer, py_check);
    v8::ValueSerializer serializer(isolate, &delegate);
    delegate.set_serializer(&serializer);

    std::vector<v8::Local<v8::ArrayBuffer>> buffers;
    if (!apply_transfer(serializer, py_transfer, context, &buffers))
        return nullptr;

    bool wrote = false;
    //: ``WriteHeader`` answers nothing: it can only fail by running out of memory, which
    //: ``WriteValue`` reports.
    serializer.WriteHeader();
    //: A delegate that refuses mid-write leaves its own exception behind even when V8 reports
    //: the write as done, so the caught exception is asked for as well.
    v8::Maybe<bool> written = serializer.WriteValue(context, value);
    if (PyErr_Occurred())
        return nullptr;
    if (!tc.HasCaught() && (written.IsNothing() || !written.FromJust()))
        written = v8::Just(false);
    if (written.IsNothing() || !written.FromJust())
    {
        //: A host writer that refused from Python keeps its own exception; anything else is
        //: V8's, and it is a page-visible refusal (DataCloneError in the host's terms).
        if (PyErr_Occurred())
            return nullptr;
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "the value could not be serialized");
        return nullptr;
    }
    (void)wrote;

    std::pair<uint8_t*, size_t> released = serializer.Release();
    PyObject* result = PyBytes_FromStringAndSize((const char*)released.first, (Py_ssize_t)released.second);
    delete[] released.first;
    if (result == nullptr)
        return nullptr;

    //: The transfer is V8's: a buffer named here is written as a transfer id, and the
    //: deserializer that accepts the same buffer takes the backing store over.  Detaching it
    //: here instead would free the store the receiver is about to attach, which is exactly
    //: how "Unable to deserialize cloned data." was measured once.
    return result;
}

PyObject* js_serializer_deserialize(js_serializer* self, PyObject* args, PyObject* kwargs)
{
    static const char* keywords[] = {"data", "transfer", "host_reader", NULL};
    PyObject* py_data = nullptr;
    PyObject* py_transfer = nullptr;
    PyObject* py_reader = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|OO", (char**)keywords, &py_data, &py_transfer,
                                     &py_reader))
        return nullptr;

    Py_buffer view;
    if (PyObject_GetBuffer(py_data, &view, PyBUF_SIMPLE) < 0)
        return nullptr;

    ExecutionScope execution((context_c*)self->py_context, (PyObject*)self);
    if (!execution)
    {
        PyBuffer_Release(&view);
        return nullptr;
    }
    v8::Isolate* isolate = execution.isolate();
    v8::Local<v8::Context> context = execution.context();
    JS_TRY

    ValueReaderDelegate delegate(context, py_reader);
    v8::ValueDeserializer deserializer(isolate, (const uint8_t*)view.buf, (size_t)view.len, &delegate);
    delegate.set_deserializer(&deserializer);
    deserializer.SetSupportsLegacyWireFormat(true);

    if (py_transfer != nullptr && py_transfer != Py_None)
    {
        Py_ssize_t count = PySequence_Size(py_transfer);
        if (count < 0)
        {
            PyErr_Clear();
            PyErr_SetString(PyExc_TypeError, "transfer must be a sequence");
            PyBuffer_Release(&view);
            return nullptr;
        }
        for (Py_ssize_t index = 0; index < count; ++index)
        {
            PyObject* entry = PySequence_GetItem(py_transfer, index);
            if (entry == nullptr)
            {
                PyBuffer_Release(&view);
                return nullptr;
            }
            v8::Local<v8::Value> item = js_from_py(entry, context);
            Py_DECREF(entry);
            if (item.IsEmpty() || !item->IsArrayBuffer())
            {
                PyBuffer_Release(&view);
                if (!PyErr_Occurred())
                PyErr_Format(PyExc_TypeError, "transfer entry %zd is not an ArrayBuffer", index);
                return nullptr;
            }
            deserializer.TransferArrayBuffer((uint32_t)index, item.As<v8::ArrayBuffer>());
        }
    }

    v8::Maybe<bool> header = deserializer.ReadHeader(context);
    if (header.IsNothing() || !header.FromJust())
    {
        PyBuffer_Release(&view);
        if (PyErr_Occurred())
            return nullptr;
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_ValueError, "the serialized stream could not be read");
        return nullptr;
    }
    v8::MaybeLocal<v8::Value> read = deserializer.ReadValue(context);
    PyBuffer_Release(&view);
    if (PyErr_Occurred())
        return nullptr;
    v8::Local<v8::Value> value;
    if (!read.ToLocal(&value) || value.IsEmpty())
    {
        if (PyErr_Occurred())
            return nullptr;
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_ValueError, "the serialized value could not be read");
        return nullptr;
    }
    return py_materialize(value, context);
}

int js_serializer_traverse(js_serializer* self, visitproc visit, void* arg)
{
    Py_VISIT(self->py_context);
    Py_VISIT(self->py_isolate);
    return 0;
}

int js_serializer_clear(js_serializer* self)
{
    Py_CLEAR(self->py_context);
    Py_CLEAR(self->py_isolate);
    return 0;
}

void js_serializer_dealloc(js_serializer* self)
{
    PyObject_GC_UnTrack(self);
    js_serializer_clear(self);
    TP_FREE_ABI3(serialization_type, self);
}

PyObject* js_serializer_repr(js_serializer* self)
{
    return PyUnicode_FromString(self->py_context == nullptr ? "<Serializer closed>" : "<Serializer>");
}

PyMethodDef js_serializer_methods[] = {
    {"serialize", (PyCFunction)PythonBoundary<js_serializer_serialize>::call,
     METH_VARARGS | METH_KEYWORDS,
     "serialize(value, transfer=(), host_writer=None, host_check=None) -> bytes"},
    {"deserialize", (PyCFunction)PythonBoundary<js_serializer_deserialize>::call,
     METH_VARARGS | METH_KEYWORDS,
     "deserialize(data, transfer=(), host_reader=None) -> object"},
    {NULL, NULL}};

PyObject* js_serializer_new(PyTypeObject* type, PyObject* args, PyObject* kwargs);

PyType_Slot js_serializer_slots[] = {
    {Py_tp_new, (void*)PythonBoundary<js_serializer_new>::call},
    {Py_tp_dealloc, (void*)PythonBoundary<js_serializer_dealloc>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_serializer_repr>::call},
    {Py_tp_traverse, (void*)PythonBoundary<js_serializer_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<js_serializer_clear>::call},
    {Py_tp_methods, js_serializer_methods},
    {Py_tp_doc, (void*)"one realm's structured-clone wire (V8's own format, host objects by callback)"},
    {0, NULL}};

PyType_Spec js_serializer_spec = {
    "pyv8.Serializer",
    sizeof(js_serializer),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    js_serializer_slots};

PyObject* js_serializer_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    PyObject* py_context = nullptr;
    static const char* keywords[] = {"context", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O", (char**)keywords, &py_context) < 0)
        return nullptr;
    if (!PyObject_TypeCheck(py_context, context_type))
    {
        PyErr_SetString(PyExc_TypeError, "context must be a Context instance");
        return nullptr;
    }
    if (!runtime_manager.ensure_initialized())
        return nullptr;
    js_serializer* self = (js_serializer*)PyType_GenericAlloc(type, 0);
    PyErr_PROPAGATE(self);
    //: The realm is entered per call, like ``PromiseResolver``, so any thread may use this.
    Py_INCREF(py_context);
    self->py_context = py_context;
    PyObject* py_isolate = ((context_c*)py_context)->py_isolate;
    if (py_isolate != nullptr)
    {
        Py_INCREF(py_isolate);
        self->py_isolate = py_isolate;
    }
    return (PyObject*)self;
}

}  // namespace

PyTypeObject* serialization_type = nullptr;
PyTypeObject* serialization_writer_type = nullptr;
PyTypeObject* serialization_reader_type = nullptr;

int serialization_type_init()
{
    serialization_writer_type = (PyTypeObject*)PyType_FromSpec(&js_serialization_writer_spec);
    if (serialization_writer_type == nullptr)
        return -1;
    serialization_reader_type = (PyTypeObject*)PyType_FromSpec(&js_serialization_reader_spec);
    if (serialization_reader_type == nullptr)
        return -1;
    serialization_type = (PyTypeObject*)PyType_FromSpec(&js_serializer_spec);
    return serialization_type == nullptr ? -1 : 0;
}
