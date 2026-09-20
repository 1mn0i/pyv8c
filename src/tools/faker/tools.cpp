#include "tools.h"

using namespace v8;

namespace faker {

    void IllegalConstructorCallback(const FunctionCallbackInfo<Value>& info) {
        info.GetIsolate()->ThrowException(v8::Exception::TypeError(V8String(info.GetIsolate(), "Illegal constructor")));
    }

    void JsFunctionCallback(const FunctionCallbackInfo<Value>& info) {
        Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();

        Local<Function> js_func = info.Data().As<Function>();
        Local<Value>* arguments = new Local<Value>[info.Length()];                                           
        for (int i = 0; i < info.Length(); i++) {                                                                    
            arguments[i] = info[i];                                                                                  
        }                                                                                                            
        TryCatch try_catch(isolate);                                                                                 
        MaybeLocal<Value> result = js_func->Call(context, info.This(), info.Length(), arguments);
        delete[] arguments; 

        if (try_catch.HasCaught()) {                                                                                 
            try_catch.ReThrow();                                                                                     
            return;                                                                                                  
        }                                                                                                            
        info.GetReturnValue().Set(result.ToLocalChecked());     
    }

    void InstallOperations(Isolate* isolate, Local<ObjectTemplate> target, Configs<OperationItem> configs, Local<Signature> signature) {
        for (int i = 0; i < configs.Size(); i++) {
            OperationItem info = configs.Get(i);
            Local<FunctionTemplate> fun_temp = FunctionTemplate::New(
                isolate, info.callback, Local<Value>(), signature, info.length, ConstructorBehavior::kThrow, info.sideEffectType
            );
            fun_temp->SetClassName(V8String(isolate, info.name));
            target->Set(isolate, info.name, fun_temp, info.propertyAttribute);
        }
    }


    void InstallConstants(Isolate* isolate, Local<ObjectTemplate> target, Configs<ConstantItem> configs) {
        for (int i = 0; i < configs.Size(); i++) {
            ConstantItem info = configs.Get(i);
            target->Set(isolate, info.name, Integer::New(isolate, info.value), info.propertyAttribute);
        }
    }


    void InstallAttributes(Isolate* isolate, Local<ObjectTemplate> target, Configs<AttributeItem> configs, Local<Signature> signature) {
        for (int i = 0; i < configs.Size(); i++) {
            AttributeItem info = configs.Get(i);
            Local<FunctionTemplate> get_fun_temp, set_fun_temp;
            std::string name = std::string(info.name);
            if (info.get_callback) {
                get_fun_temp = FunctionTemplate::New(
                    isolate, info.get_callback, Local<Value>(), signature, 0, ConstructorBehavior::kThrow, info.sideEffectType
                );
                std::string get_name = std::string("get ", 4) + name;
                get_fun_temp->SetClassName(V8String(isolate, get_name.data()));
            }
            if (info.set_callback) {
                set_fun_temp = FunctionTemplate::New(
                    isolate, info.set_callback, Local<Value>(), signature, 1, ConstructorBehavior::kThrow, info.sideEffectType
                );
                std::string set_name = std::string("set ", 4) + name;
                set_fun_temp->SetClassName(V8String(isolate, set_name.data()));
            }
            target->SetAccessorProperty(V8String(isolate, info.name), get_fun_temp, set_fun_temp, info.propertyAttribute);
        }
    }



} // namespace faker