#ifndef FAKER_TOOLS_H
#define FAKER_TOOLS_H

#include <v8.h>


namespace faker {

#define HOME_NAME "faker"

#define V8String(isolate, str) v8::String::NewFromUtf8(isolate, str).ToLocalChecked()

template <typename T>
static T RsMaybe(v8::Maybe<T> maybe) {
    T value;
    if (maybe.To(&value)) {
        return value;
    }
    else {
        return value;
    }
}

template <typename T>
static v8::Local<T> RsMaybe(v8::MaybeLocal<T> maybe) {
    v8::Local<T> value;
    if (maybe.ToLocal(&value)) {
        return value;
    }
    else {
        return value;
    }
}

struct OperationItem {
    const char* name = "";
    int length = 0;
    v8::FunctionCallback callback = nullptr;
    v8::PropertyAttribute propertyAttribute;
    //Local<Value> data;
    v8::SideEffectType sideEffectType = v8::SideEffectType::kHasSideEffect;
};

template<typename T>
class Configs {
public:
    Configs(T* ary, int size) : info_(ary), size_(size) {}
    T Get(int index) { return info_[index]; }
    int Size() { return size_; }
private:
    T* info_ = nullptr;
    int size_ = 0;
};

#define CONFIGS(instance) Configs(instance, sizeof(instance)/sizeof(decltype(instance[0])))

#define ARGSNUMBER(instance) sizeof(instance)/sizeof(decltype(instance[0]))

#define MAXARGSLENGTH   10


void IllegalConstructorCallback(const v8::FunctionCallbackInfo<v8::Value>& info);
void JsFunctionCallback(const v8::FunctionCallbackInfo<v8::Value>& info);

void InstallOperations(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> target, Configs<OperationItem> configs, v8::Local<v8::Signature> signature);

struct ConstantItem {
    const char* name = "";
    int value = 0;
    v8::PropertyAttribute propertyAttribute;
};

void InstallConstants(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> target, Configs<ConstantItem> configs);

struct AttributeItem {
    const char* name = "";
    v8::FunctionCallback get_callback = nullptr;
    v8::FunctionCallback set_callback = nullptr;
    v8::PropertyAttribute propertyAttribute;
    v8::SideEffectType sideEffectType = v8::SideEffectType::kHasSideEffect;
};

void InstallAttributes(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> target, Configs<AttributeItem> configs, v8::Local<v8::Signature> signature);

} //namespace faker 

#endif