#ifndef CONVERT_H
#define CONVERT_H

#include <Python.h>
#include <v8.h>
#include <memory>
#include <new>

class PythonArguments {
public:
    explicit PythonArguments(size_t size) : size_(size) {
        if (size > 8) {
            heap_.reset(new (std::nothrow) PyObject*[size]());
            values_ = heap_.get();
            if (!values_) PyErr_NoMemory();
        }
    }
    ~PythonArguments() {
        if (values_) for (size_t i = 0; i < size_; ++i) Py_XDECREF(values_[i]);
    }
    PythonArguments(const PythonArguments&) = delete;
    bool valid() const { return values_ != nullptr; }
    PyObject* const* data() const { return values_; }
    size_t size() const { return size_; }
    bool convert(const v8::FunctionCallbackInfo<v8::Value>& info,
                 v8::Local<v8::Context> context, PyObject* receiver = nullptr);
private:
    size_t size_;
    PyObject* inline_[8] = {};
    std::unique_ptr<PyObject*[]> heap_;
    PyObject** values_ = inline_;
};

class V8ValueArguments
{
public:
    static constexpr int kInlineCapacity = 16;

    explicit V8ValueArguments(int length)
        : values_(inline_values_)
    {
        if (length > kInlineCapacity)
        {
            heap_values_.reset(new (std::nothrow) v8::Local<v8::Value>[length]);
            values_ = heap_values_.get();
        }
    }

    V8ValueArguments(const V8ValueArguments&) = delete;
    V8ValueArguments& operator=(const V8ValueArguments&) = delete;

    bool valid() const { return values_ != nullptr; }
    v8::Local<v8::Value>* data() { return values_; }

private:
    v8::Local<v8::Value> inline_values_[kInlineCapacity];
    std::unique_ptr<v8::Local<v8::Value>[]> heap_values_;
    v8::Local<v8::Value>* values_;
};

// The two ways a JavaScript value becomes Python data, and the one way back:
//
// ``py_from_js`` is the *handle* reading -- the reading a bridge callback does.  A
// primitive arrives as its value, a host's own object arrives as itself, and a
// JavaScript object arrives as a ``JSObject`` handle: nothing is enumerated, no
// prototype is probed, no accessor runs, so reading can never run page code.
//
// ``py_materialize`` is the *snapshot* reading -- the reading an algorithm asks for
// when it needs the data itself.  Arrays and plain objects are walked and materialized
// into Python containers (through ``JSObject.to_python()``); accessors may run, and a
// getter's failure arrives as a Python error.
PyObject *py_from_js(v8::Local<v8::Value> js_value, v8::Local<v8::Context> context);
PyObject *py_materialize(v8::Local<v8::Value> value, v8::Local<v8::Context> context);

// Python becomes JavaScript in the realm given.  The two-argument form serves the
// realm it is handed; the three-argument form names the isolate the value is built
// for, for the one crossing that hands a value to another isolate.  Python ``bytes``
// arrive as a fresh ``ArrayBuffer``, whole.
v8::Local<v8::Value> js_from_py(PyObject *py_value, v8::Local<v8::Context> context);
v8::Local<v8::Value> js_from_py(PyObject *py_value, v8::Local<v8::Context> context, v8::Isolate* isolate);

void jss_from_pys(PyObject *py_args, v8::Local<v8::Value> *js_args, v8::Local<v8::Context> context);
#endif
