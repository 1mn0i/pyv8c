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
            heap_values_.reset(new (std::nothrow) Local<Value>[length]);
            values_ = heap_values_.get();
        }
    }

    V8ValueArguments(const V8ValueArguments&) = delete;
    V8ValueArguments& operator=(const V8ValueArguments&) = delete;

    bool valid() const { return values_ != nullptr; }
    Local<Value>* data() { return values_; }

private:
    Local<Value> inline_values_[kInlineCapacity];
    std::unique_ptr<Local<Value>[]> heap_values_;
    Local<Value>* values_;
};

PyObject *py_from_js(Local<Value> js_value, Local<Context> context);
Local<Value> js_from_py(PyObject *py_value, Local<Context> context);

PyObject *pys_from_jss(const FunctionCallbackInfo<Value> &js_args, Local<Context> context);
void jss_from_pys(PyObject *py_args, Local<Value> *js_args, Local<Context> context);

Local<Object> pyexception_to_jsexception(PyObject* value, Local<Context> context);
#endif
