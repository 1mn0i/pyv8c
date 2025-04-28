#include "EventTarget.h"

namespace faker {

#define Callback(name)                                                                                               \
        Isolate* isolate = info.GetIsolate();                                                                        \
        Local<Context> context = isolate->GetCurrentContext();                                                       \
        Local<Object> global = context->Global();                                                                    \
        Local<Object> faker = global->Get(context, V8String(isolate, HOME_NAME)).ToLocalChecked().As<Object>();      \
        Local<Value> func = faker->Get(context, V8String(isolate, name)).ToLocalChecked();                           \
        if (func.IsEmpty() || !func->IsFunction()) {                                                                 \
            info.GetIsolate()->ThrowException(v8::Exception::Error(V8String(info.GetIsolate(), "patch "#name)));     \
            return;                                                                                                  \
        }                                                                                                            \
        Local<Value>* arguments = new Local<Value>[info.Length()];                                                   \
        for (int i = 0; i < info.Length(); i++) {                                                                    \
            arguments[i] = info[i];                                                                                  \
        }                                                                                                            \
        TryCatch try_catch(isolate);                                                                                 \
        MaybeLocal<Value> result = func.As<Function>()->Call(context, info.This(), info.Length(), arguments);        \
        delete[] arguments;                                                                                          \
        if (try_catch.HasCaught()) {                                                                                 \
            try_catch.ReThrow();                                                                                     \
            return;                                                                                                  \
        }                                                                                                            \
        info.GetReturnValue().Set(result.ToLocalChecked());                                                          

    void EventTargetConstructor(const FunctionCallbackInfo<Value>& info) {
        if (!info.IsConstructCall()) {
            info.GetIsolate()->ThrowException(v8::Exception::TypeError(V8String(info.GetIsolate(), "Failed to construct 'EventTarget': Please use the 'new' operator, this DOM object constructor cannot be called as a function.")));
            return;
        }
        Callback("EventTarget_constructor")
    }

    void AddEventListenerCallback(const FunctionCallbackInfo<Value>& info) {
        Callback("EventTarget_addEventListener")
    }
    void DispatchEventCallback(const FunctionCallbackInfo<Value>& info) {
        Callback("EventTarget_dispatchEvent")
    }
    void RemoveEventListenerCallback(const FunctionCallbackInfo<Value>& info) {
        Callback("EventTarget_removeEventListener")
    }


} // namespace faker
