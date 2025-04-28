#ifndef FAKER_TOOLS_H
#define FAKER_TOOLS_H

#include <v8.h>


using namespace v8;

namespace faker {

#define HOME_NAME "faker"

#define V8String(isolate, str) String::NewFromUtf8(isolate, str).ToLocalChecked()

template <typename T>
static T RsMaybe(Maybe<T> maybe) {
    T value;
    if (maybe.To(&value)) {
        return value;
    }
    else {
        return value;
    }
}

template <typename T>
static Local<T> RsMaybe(MaybeLocal<T> maybe) {
    Local<T> value;
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
    FunctionCallback callback = nullptr;
    PropertyAttribute propertyAttribute;
    //Local<Value> data;
    SideEffectType sideEffectType = SideEffectType::kHasSideEffect;
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


void IllegalConstructorCallback(const FunctionCallbackInfo<Value>& info);
void JsFunctionCallback(const FunctionCallbackInfo<Value>& info);

void InstallOperations(Isolate* isolate, Local<ObjectTemplate> target, Configs<OperationItem> configs, Local<Signature> signature);

struct ConstantItem {
    const char* name = "";
    int value = 0;
    PropertyAttribute propertyAttribute;
};

void InstallConstants(Isolate* isolate, Local<ObjectTemplate> target, Configs<ConstantItem> configs);

struct AttributeItem {
    const char* name = "";
    FunctionCallback get_callback = nullptr;
    FunctionCallback set_callback = nullptr;
    PropertyAttribute propertyAttribute;
    SideEffectType sideEffectType = SideEffectType::kHasSideEffect;
};

void InstallAttributes(Isolate* isolate, Local<ObjectTemplate> target, Configs<AttributeItem> configs, Local<Signature> signature);

} //namespace faker 

#endif