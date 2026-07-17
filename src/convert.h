#ifndef CONVERT_H
#define CONVERT_H

#include <Python.h>
#include <v8.h>
#include <memory>
#include <new>

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

PyObject *py_from_js(v8::Local<v8::Value> js_value, v8::Local<v8::Context> context);
v8::Local<v8::Value> js_from_py(PyObject *py_value, v8::Local<v8::Context> context);
v8::Local<v8::Value> js_from_py(PyObject *py_value, v8::Local<v8::Context> context, v8::Isolate* isolate);

PyObject *pys_from_jss(const v8::FunctionCallbackInfo<v8::Value> &js_args, v8::Local<v8::Context> context);
void jss_from_pys(PyObject *py_args, v8::Local<v8::Value> *js_args, v8::Local<v8::Context> context);

v8::Local<v8::Object> pyexception_to_jsexception(PyObject* value, v8::Local<v8::Context> context);
#endif
