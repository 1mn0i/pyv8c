#ifndef PYCLASS_CROSS_ORIGIN_HANDLERS_H
#define PYCLASS_CROSS_ORIGIN_HANDLERS_H

#include <v8.h>

bool cross_origin_access_check(
    v8::Local<v8::Context> accessing_context,
    v8::Local<v8::Object> accessed_object,
    v8::Local<v8::Value> unused_data);

void cross_origin_failed_access(
    v8::Local<v8::Object> holder,
    v8::AccessType type,
    v8::Local<v8::Value> unused_data);

v8::Intercepted cross_origin_named_getter(
    v8::Local<v8::Name> name,
    const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted cross_origin_named_setter(
    v8::Local<v8::Name> name,
    v8::Local<v8::Value> value,
    const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted cross_origin_named_query(
    v8::Local<v8::Name> name,
    const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted cross_origin_named_deleter(
    v8::Local<v8::Name> name,
    const v8::PropertyCallbackInfo<v8::Boolean>& info);
void cross_origin_named_enumerator(
    const v8::PropertyCallbackInfo<v8::Array>& info);

v8::Intercepted cross_origin_indexed_getter(
    uint32_t index,
    const v8::PropertyCallbackInfo<v8::Value>& info);
v8::Intercepted cross_origin_indexed_setter(
    uint32_t index,
    v8::Local<v8::Value> value,
    const v8::PropertyCallbackInfo<void>& info);
v8::Intercepted cross_origin_indexed_query(
    uint32_t index,
    const v8::PropertyCallbackInfo<v8::Integer>& info);
v8::Intercepted cross_origin_indexed_deleter(
    uint32_t index,
    const v8::PropertyCallbackInfo<v8::Boolean>& info);
void cross_origin_indexed_enumerator(
    const v8::PropertyCallbackInfo<v8::Array>& info);

#endif
