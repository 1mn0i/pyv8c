#ifndef FAKER_WINDOW_H
#define FAKER_WINDOW_H

#include <v8.h>
#include "../tools.h"
#include "EventTarget.h"

using namespace v8;



namespace faker {

#define WINDOW_ATTRIBUTE_SET_CALLBACK(name) [](const v8::FunctionCallbackInfo<v8::Value>& info) {                                                       \
    auto isolate = info.GetIsolate();                                                                                                                   \
    auto context = isolate->GetCurrentContext();                                                                                                        \
    if (info.Length() < 1) {                                                                                                                            \
        info.GetIsolate()->ThrowException(v8::Exception::TypeError(String::NewFromUtf8(info.GetIsolate(), "Not Enough Arguments").ToLocalChecked()));   \
        return;                                                                                                                                         \
    }                                                                                                                                                   \
    info.This()->CreateDataProperty(context, V8String(isolate, name), info[0]);                                                                         \
}

    void WindowAttributeGetCallback(const v8::FunctionCallbackInfo<v8::Value>& info);

    Local<ObjectTemplate> WindowInterface(Isolate* isolate);

} // namespace faker


#endif