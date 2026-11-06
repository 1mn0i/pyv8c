#ifndef PYV8_V8_COMPAT_H
#define PYV8_V8_COMPAT_H

#include <v8.h>
#include <v8-fast-api-calls.h>
#include <v8-version.h>
#include <span>

#if V8_MAJOR_VERSION < 15 || (V8_MAJOR_VERSION == 15 && V8_MINOR_VERSION < 4)
#error "pyv8c requires V8 15.4 or newer"
#endif

namespace v8_compat {

using PropertySetterResult = v8::Boolean;
inline void setter_rejected(const v8::PropertyCallbackInfo<PropertySetterResult>& info) {
    info.GetReturnValue().Set(false);
}

inline v8::Local<v8::External> external_new(v8::Isolate* isolate, void* value)
{
    return v8::External::New(
        isolate, value, v8::kExternalPointerTypeTagDefault);
}

template <typename T>
inline void* external_value(v8::Local<T> value)
{
    v8::Local<v8::External> external = value.template As<v8::External>();
    return external->Value(v8::kExternalPointerTypeTagDefault);
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
    (void)context;
    return v8::Isolate::GetCurrent();
}

inline v8::Local<v8::Object> global_template_object(
    v8::Local<v8::Context> context)
{
    return context->Global();
}
inline std::span<const v8::CFunction> c_function_overloads(
    const v8::CFunction* data, size_t size)
{
    return std::span<const v8::CFunction>(data, size);
}


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
    return info.This();
}

}  // namespace v8_compat

#endif
