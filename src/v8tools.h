#ifndef PYV8ENV_TOOLS_H
#define PYV8ENV_TOOLS_H

#include <Python.h>
#include "pyv8.h"
#include "pyclass.h"
#include <v8.h>


using namespace v8;

#define Set_Config_Value(func, func_attr, config, config_attr)         \
    {                                                                  \
        PyObject* ret = PyObject_GetAttrString(func, func_attr);       \
        if (ret == NULL || !PyLong_Check(ret)) {                       \
            config.config_attr = 0;                                    \
        }                                                              \
        else {                                                         \
            config.config_attr = PyLong_AsLong(ret);                   \
        }                                                              \
        Py_XDECREF(ret);                                               \
    }


namespace bindings {

    void FakerJsFunctionCallback(const FunctionCallbackInfo<Value>& info);
    void FakerJsFunctionAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info);
    void ConstructAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info);

    enum class CallbackType {
        kPy = 0,
        kJs = 1,
    };

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

        const char* name;
        v8::FunctionCallback callback_for_get;
        v8::FunctionCallback callback_for_set;
        Local<External> get_method;
        Local<External> set_method;
        unsigned v8_property_attribute : 3;       // v8::PropertyAttribute
        unsigned location : 2;                    // FlagLocation
        unsigned receiver_check : 1;              // FlagReceiverCheck
        unsigned cross_origin_check_for_get : 1;  // FlagCrossOriginCheck
        unsigned cross_origin_check_for_set : 1;  // FlagCrossOriginCheck
        unsigned v8_side_effect : 2;              // v8::SideEffectType
    };

    // Web IDL operation
    struct OperationConfig {
        OperationConfig& operator=(const OperationConfig&) = delete;

        const char* name;
        Local<Value> callback_data;
        v8::FunctionCallback callback;
        unsigned length : 8;
        unsigned v8_property_attribute : 3;  // v8::PropertyAttribute
        unsigned location : 2;               // FlagLocation
        unsigned world : 2;                  // FlagWorld
        unsigned receiver_check : 1;         // FlagReceiverCheck
        unsigned cross_origin_check : 1;     // FlagCrossOriginCheck
        unsigned v8_side_effect : 2;         // v8::SideEffectType
    };

    struct ConstantValueConfig {
        ConstantValueConfig& operator=(const ConstantValueConfig&) = delete;

        const char* name;
        int64_t value;
    };

    struct ConstantCallbackConfig {
        ConstantCallbackConfig& operator=(const ConstantCallbackConfig&) = delete;

        const char* name;
        const char* callback_name;
        v8::AccessorNameGetterCallback callback;
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

    void InstallAttributes(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs);

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
        PyObject* callback_data_owner);

    void InstallConstantValue(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        ConstantValueConfig& configs);

    void InstallConstantValues(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs);


    void InstallConstantCallbacks(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs);

    void InstallExposedConstruct(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> instance_template, const char* name);


    void InstallExposedConstruct(v8::Local<v8::Context> context, v8::Local<v8::Object> instance_object, const char* name);

} // namespace bindings

#endif
