#ifndef PYV8ENV_TOOLS_H
#define PYV8ENV_TOOLS_H

#include "bridge_base.h"
#include <Python.h>

#include "pyclass.h"
#include <v8.h>


namespace bindings {


    void ConstructAccessorNameGetterCallback(v8::Local<v8::Name> property, const v8::PropertyCallbackInfo<v8::Value>& info);

    enum class FlagLocation {
        kInstance = 0,
        kPrototype = 1,
        kInterface = 2,
    };
    // In which world the property is defined
    enum class FlagWorld {
        kMainWorld = 1 << 0,
        kNonMainWorlds = 1 << 1,
        kAllWorlds = kMainWorld | kNonMainWorlds,
    };
    // v8::Signature check against the receiver object
    enum class FlagReceiverCheck {
        kCheck,
        kDoNotCheck,
    };
    // Cross origin access check
    enum class FlagCrossOriginCheck {
        kCheck,
        kDoNotCheck,
    };

    enum class FunctionKind {
        kAttributeGet,
        kAttributeSet,
        kOperation,
    };

    // Web IDL attribute
    struct AttributeConfig {
        AttributeConfig& operator=(const AttributeConfig&) = delete;

        const char* name = nullptr;
        v8::FunctionCallback callback_for_get = nullptr;
        v8::FunctionCallback callback_for_set = nullptr;
        v8::Local<v8::External> get_method;
        v8::Local<v8::External> set_method;
        unsigned v8_property_attribute : 3 = 0;       // v8::PropertyAttribute
        unsigned location : 2 = 0;                    // FlagLocation
        unsigned receiver_check : 1 = 0;              // FlagReceiverCheck
        unsigned cross_origin_check_for_get : 1 = 0;  // FlagCrossOriginCheck
        unsigned cross_origin_check_for_set : 1 = 0;  // FlagCrossOriginCheck
        unsigned v8_side_effect : 2 = 0;              // v8::SideEffectType
    };

    // Web IDL operation
    struct OperationConfig {
        OperationConfig& operator=(const OperationConfig&) = delete;

        const char* name = nullptr;
        v8::Local<v8::Value> callback_data;
        v8::FunctionCallback callback = nullptr;
        unsigned length : 8 = 0;
        unsigned v8_property_attribute : 3 = 0;  // v8::PropertyAttribute
        unsigned location : 2 = 0;               // FlagLocation
        unsigned world : 2 = 0;                  // FlagWorld
        unsigned receiver_check : 1 = 0;         // FlagReceiverCheck
        unsigned cross_origin_check : 1 = 0;     // FlagCrossOriginCheck
        unsigned v8_side_effect : 2 = 0;         // v8::SideEffectType
    };

    struct ConstantValueConfig {
        ConstantValueConfig& operator=(const ConstantValueConfig&) = delete;

        const char* name;
        int64_t value;
    };

    struct ExposedConstructConfig {
        ExposedConstructConfig& operator=(const ExposedConstructConfig&) = delete;

        const char* name;
        v8::AccessorNameGetterCallback callback;
    };

    void InstallAttribute(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        AttributeConfig& config);

    bool InstallAttributes(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs,
        PyObject* callback_data_owner, context_c* owner);

    void InstallOperation(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        const OperationConfig& config);

    bool InstallOperations(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs,
        PyObject* callback_data_owner, context_c* owner);

    void InstallConstantValue(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        ConstantValueConfig& configs);

    bool InstallConstantValues(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs);


    void InstallExposedConstruct(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> instance_template, const char* name);


    void InstallExposedConstruct(v8::Local<v8::Context> context, v8::Local<v8::Object> instance_object, const char* name);

} // namespace bindings

#endif
