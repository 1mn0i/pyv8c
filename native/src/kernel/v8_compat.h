#ifndef V8N_V8_COMPAT_H
#define V8N_V8_COMPAT_H

// The handful of places where V8 15 spells something differently from the
// releases the previous binding also supported.  Kept apart from the kernel so
// the difference is visible in one file instead of scattered through call
// sites, and so a future V8 bump has exactly one place to look.

#include "v8n/v8.h"

#include <v8-version.h>

namespace v8n::v8c {

// V8 15 tags every external pointer.  The default tag keeps the previous
// untyped behaviour while still satisfying the type-checked API.
inline v8::Local<v8::External> external_new(v8::Isolate* isolate, void* value)
{
#if V8_MAJOR_VERSION >= 15
    return v8::External::New(isolate, value, v8::kExternalPointerTypeTagDefault);
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

// Read an embedder-data slot as a pointer, tolerating the empty and non-external
// shapes that appear before a context finishes installing its slots.
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

// V8 15 dropped Local<Context>::GetIsolate in favour of the thread's current
// isolate, which is meaningful precisely because this kernel only ever runs V8
// on the thread that owns the isolate.
inline v8::Isolate* context_isolate(v8::Local<v8::Context> context)
{
#if V8_MAJOR_VERSION >= 15
    (void)context;
    return v8::Isolate::GetCurrent();
#else
    return context->GetIsolate();
#endif
}

// The object a template's instances inherit from.  V8 15 renamed the accessor.
inline v8::Local<v8::Object> global_template_object(v8::Local<v8::Context> context)
{
#if V8_MAJOR_VERSION >= 15
    return context->Global();
#else
    return context->Global()->GetPrototype().As<v8::Object>();
#endif
}

// The receiver of an installed host callback.  Function callbacks and property
// callbacks disagree on which of This()/Holder() means the receiver, and V8 15
// changed both.
template <typename T>
inline v8::Local<v8::Object> callback_holder(const v8::PropertyCallbackInfo<T>& info)
{
    return info.Holder();
}

template <typename T>
inline v8::Local<v8::Object> callback_holder(const v8::FunctionCallbackInfo<T>& info)
{
#if V8_MAJOR_VERSION >= 15
    return info.This();
#else
    return info.Holder();
#endif
}

template <typename T>
inline v8::Local<v8::Object> property_callback_receiver(
    const v8::PropertyCallbackInfo<T>& info)
{
#if V8_MAJOR_VERSION >= 15
    return info.Holder();
#else
    return info.This();
#endif
}

}  // namespace v8n::v8c

#endif
