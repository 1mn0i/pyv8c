#ifndef PYCLASS_CROSS_ORIGIN_HANDLERS_H
#define PYCLASS_CROSS_ORIGIN_HANDLERS_H

#include <v8.h>

using namespace v8;

bool cross_origin_access_check(
    Local<Context> accessing_context,
    Local<Object> accessed_object,
    Local<Value> unused_data);

Intercepted cross_origin_named_getter(
    Local<Name> name,
    const PropertyCallbackInfo<Value>& info);
Intercepted cross_origin_named_setter(
    Local<Name> name,
    Local<Value> value,
    const PropertyCallbackInfo<void>& info);
Intercepted cross_origin_named_query(
    Local<Name> name,
    const PropertyCallbackInfo<Integer>& info);
Intercepted cross_origin_named_deleter(
    Local<Name> name,
    const PropertyCallbackInfo<Boolean>& info);
void cross_origin_named_enumerator(
    const PropertyCallbackInfo<Array>& info);

Intercepted cross_origin_indexed_getter(
    uint32_t index,
    const PropertyCallbackInfo<Value>& info);
Intercepted cross_origin_indexed_setter(
    uint32_t index,
    Local<Value> value,
    const PropertyCallbackInfo<void>& info);
Intercepted cross_origin_indexed_query(
    uint32_t index,
    const PropertyCallbackInfo<Integer>& info);
Intercepted cross_origin_indexed_deleter(
    uint32_t index,
    const PropertyCallbackInfo<Boolean>& info);
void cross_origin_indexed_enumerator(
    const PropertyCallbackInfo<Array>& info);

#endif
