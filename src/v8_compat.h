#ifndef PYV8_V8_COMPAT_H
#define PYV8_V8_COMPAT_H

#include <v8.h>
#include <v8-fast-api-calls.h>
#include <v8-version.h>

#if V8_MAJOR_VERSION >= 15
#include <span>
#endif

namespace v8_compat {

inline v8::Local<v8::External> external_new(v8::Isolate* isolate, void* value)
{
#if V8_MAJOR_VERSION >= 15
    return v8::External::New(
        isolate, value, v8::kExternalPointerTypeTagDefault);
#else
    return v8::External::New(isolate, value);
#endif
}

template <typename T>
inline void* external_value(v8::Local<T> value)
{
    v8::Local<v8::External> external = value.template As<v8::External>();
#if V8_MAJOR_VERSION >= 15
    return external->Value(v8::kExternalPointerTypeTagDefault);
#else
    return external->Value();
#endif
}

template <typename T, typename DataType>
inline T* external_pointer_or_null(v8::Local<DataType> data)
{
    if (data.IsEmpty() || !data->IsValue())
        return nullptr;
    v8::Local<v8::Value> value = data.template As<v8::Value>();
    if (!value->IsExternal())
        return nullptr;
    return static_cast<T*>(external_value(value));
}

template <typename T>
inline T* context_embedder_pointer(v8::Local<v8::Context> context, int index)
{
    return external_pointer_or_null<T>(context->GetEmbedderData(index));
}

inline v8::Isolate* context_isolate(v8::Local<v8::Context> context)
{
#if V8_MAJOR_VERSION >= 15
    (void)context;
    return v8::Isolate::GetCurrent();
#else
    return context->GetIsolate();
#endif
}

inline v8::Local<v8::Object> global_template_object(
    v8::Local<v8::Context> context)
{
#if V8_MAJOR_VERSION >= 15
    return context->Global();
#else
    return context->Global()->GetPrototype().As<v8::Object>();
#endif
}

#if V8_MAJOR_VERSION >= 15
inline std::span<const v8::CFunction> c_function_overloads(
    const v8::CFunction* data, size_t size)
{
    return std::span<const v8::CFunction>(data, size);
}
#else
inline v8::MemorySpan<const v8::CFunction> c_function_overloads(
    const v8::CFunction* data, size_t size)
{
    return v8::MemorySpan<const v8::CFunction>(data, size);
}
#endif

template <typename T>
inline v8::Local<v8::Object> callback_holder(
    const v8::PropertyCallbackInfo<T>& info)
{
    return info.Holder();
}

template <typename T>
inline v8::Local<v8::Object> callback_holder(
    const v8::FunctionCallbackInfo<T>& info)
{
#if V8_MAJOR_VERSION >= 15
    return info.This();
#else
    return info.Holder();
#endif
}

}  // namespace v8_compat

#endif
