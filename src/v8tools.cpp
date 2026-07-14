#include "v8tools.h"
#include "convert.h"

using namespace v8;


namespace bindings {


    void FakerJsFunctionCallback(const FunctionCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        Local<Object> global = context->Global();
        Local<Object> faker = global
            ->Get(context, runtime_state.v8_string(V8StringId::Faker))
            .ToLocalChecked()
            .As<Object>();
        PyObject* cb_name = (PyObject*)info.Data().As<External>()->Value();
        Local<String> cb = V8String(isolate, PyUnicode_AsUTF8_abi3(cb_name));
        Local<Value> func = faker->Get(context, cb).ToLocalChecked();
        if (func.IsEmpty() || !func->IsFunction()) {
            info.GetIsolate()->ThrowException(v8::Exception::Error(String::Concat(isolate, V8String(isolate, "patch callback "), cb)));
            return;
        }
        V8ValueArguments arguments(info.Length());
        if (!arguments.valid()) {
            isolate->ThrowException(v8::Exception::Error(
                V8String(isolate, "failed to allocate JavaScript arguments")));
            return;
        }
        for (int i = 0; i < info.Length(); i++) {
            arguments.data()[i] = info[i];
        }
        TryCatch try_catch(isolate);
        MaybeLocal<Value> result = func.As<Function>()->Call(
            context, info.This(), info.Length(), arguments.data());
        if (try_catch.HasCaught()) {
            try_catch.ReThrow();
            return;
        }
        info.GetReturnValue().Set(result.ToLocalChecked());
    }

    void FakerJsFunctionAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        Local<Object> global = context->Global();
        Local<Object> faker = global
            ->Get(context, runtime_state.v8_string(V8StringId::Faker))
            .ToLocalChecked()
            .As<Object>();
        Local<String> cb = info.Data().As<String>();
        Local<Value> func = faker->Get(context, cb).ToLocalChecked();
        if (func.IsEmpty() || !func->IsFunction()) {
            info.GetIsolate()->ThrowException(v8::Exception::Error(String::Concat(isolate, V8String(isolate, "patch callback "), cb)));
            return;
        }

        Local<Value>* arguments = new Local<Value>[0];
        TryCatch try_catch(isolate);
        MaybeLocal<Value> result = func.As<Function>()->Call(context, info.This(), 0, arguments);
        delete[] arguments;

        if (try_catch.HasCaught()) {
            try_catch.ReThrow();
            return;
        }
        info.GetReturnValue().Set(result.ToLocalChecked());
    }

    void ConstructAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        context_c* py_ctx = (context_c*)context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();

        String::Utf8Value property_name(isolate, property);
        PyObject* name = PyUnicode_FromString(*property_name);
        PyObject* py_cls = PyDict_GetItem(py_ctx->class_template_cache, name);
        if (py_cls == NULL) {
            Py_XDECREF(name);
            info.GetReturnValue().SetUndefined();
            return;
        }
        Local<FunctionTemplate> interface_template = ((py_class*)py_cls)->templ.Get(isolate);
        Py_XDECREF(name);
        v8::Local<v8::Function> interface_object;
        bool get_interface_object =
            interface_template->GetFunction(context).ToLocal(&interface_object);
        if (get_interface_object) {
            info.GetReturnValue().Set(interface_object);
        }
        else {
            info.GetReturnValue().SetUndefined();
        }
        
    }

    template <FunctionKind kind, typename Config>
    v8::FunctionCallback GetConfigCallback(const Config& config);
    template <>
    v8::FunctionCallback GetConfigCallback<FunctionKind::kAttributeGet>(
        const AttributeConfig& config) {
        return config.callback_for_get;
    }
    template <>
    v8::FunctionCallback GetConfigCallback<FunctionKind::kAttributeSet>(
        const AttributeConfig& config) {
        return config.callback_for_set;
    }
    template <>
    v8::FunctionCallback GetConfigCallback<FunctionKind::kOperation>(
        const OperationConfig& config) {
        return config.callback;
    }

    template <FunctionKind kind, typename Config>
    int GetConfigLength(const Config& config);
    template <>
    int GetConfigLength<FunctionKind::kAttributeGet>(
        const AttributeConfig& config) {
        return 0;
    }
    template <>
    int GetConfigLength<FunctionKind::kAttributeSet>(
        const AttributeConfig& config) {
        return 1;
    }
    template <>
    int GetConfigLength<FunctionKind::kOperation>(
        const OperationConfig& config) {
        return config.length;
    }

    template <FunctionKind kind, typename Config>
    inline Local<Value> GetConfigCallbackName(const Config& config);
    template <>
    inline Local<Value> GetConfigCallbackName<FunctionKind::kAttributeGet>(
        const AttributeConfig& config) {
        return config.get_method;
    }
    template <>
    inline Local<Value> GetConfigCallbackName<FunctionKind::kAttributeSet>(
        const AttributeConfig& config) {
        return config.set_method;
    }
    template <>
    inline Local<Value> GetConfigCallbackName<FunctionKind::kOperation>(
        const OperationConfig& config) {
        return config.js_method;
    }


    template <FunctionKind kind, typename Config>
    v8::SideEffectType GetConfigSideEffect(const Config& config);
    template <>
    v8::SideEffectType GetConfigSideEffect<FunctionKind::kAttributeGet>(
        const AttributeConfig& config) {
        return static_cast<v8::SideEffectType>(config.v8_side_effect);
    }
    template <>
    v8::SideEffectType GetConfigSideEffect<FunctionKind::kAttributeSet>(
        const AttributeConfig& config) {
        return v8::SideEffectType::kHasSideEffect;
    }
    template <>
    v8::SideEffectType GetConfigSideEffect<FunctionKind::kOperation>(
        const OperationConfig& config) {
        return static_cast<v8::SideEffectType>(config.v8_side_effect);
    }

    template <FunctionKind kind, typename Config>
    FlagCrossOriginCheck GetConfigCrossOriginCheck(
        const Config& config);
    template <>
    FlagCrossOriginCheck
        GetConfigCrossOriginCheck<FunctionKind::kAttributeGet>(
            const AttributeConfig& config) {
        return static_cast<FlagCrossOriginCheck>(
            config.cross_origin_check_for_get);
    }
    template <>
    FlagCrossOriginCheck
        GetConfigCrossOriginCheck<FunctionKind::kAttributeSet>(
            const AttributeConfig& config) {
        return static_cast<FlagCrossOriginCheck>(
            config.cross_origin_check_for_set);
    }
    template <>
    FlagCrossOriginCheck
        GetConfigCrossOriginCheck<FunctionKind::kOperation>(
            const OperationConfig& config) {
        return static_cast<FlagCrossOriginCheck>(
            config.cross_origin_check);
    }

    template <FunctionKind kind, typename Config>
    v8::Local<v8::FunctionTemplate> CreateFunctionTemplate(
        v8::Isolate* isolate,
        v8::Local<v8::Signature> signature,
        v8::Local<v8::String> name,
        const Config& config,
        const v8::CFunction* v8_cfunction_table_data = nullptr,
        uint32_t v8_cfunction_table_size = 0) {
        v8::FunctionCallback callback = GetConfigCallback<kind>(config);
        if (!callback)
            return v8::Local<v8::FunctionTemplate>();

        int length = GetConfigLength<kind>(config);
        v8::SideEffectType v8_side_effect = GetConfigSideEffect<kind>(config);
        v8::Local<v8::FunctionTemplate> function_template;
        function_template = v8::FunctionTemplate::NewWithCFunctionOverloads(
            isolate, callback, GetConfigCallbackName<kind>(config), signature, length,
            v8::ConstructorBehavior::kThrow, v8_side_effect,
            { v8_cfunction_table_data, v8_cfunction_table_size });

        function_template->SetClassName(name);
        function_template->SetAcceptAnyReceiver(
            GetConfigCrossOriginCheck<kind>(config) == FlagCrossOriginCheck::kDoNotCheck);

        return function_template;
    }


    void InstallAttribute(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        AttributeConfig& config) {

        FlagLocation location =
            static_cast<FlagLocation>(config.location);
        if (static_cast<FlagReceiverCheck>(
            config.receiver_check) == FlagReceiverCheck::kDoNotCheck ||
            location == FlagLocation::kInterface)
            signature = v8::Local<v8::Signature>();

        //StringView name_as_view(config.name);
        std::string name_as_view = std::string(config.name);
        v8::Local<v8::String> name = V8String(isolate, name_as_view.data());
        v8::Local<v8::String> get_name = V8String(isolate, (std::string("get ", 4) + name_as_view).data());
        v8::Local<v8::String> set_name = V8String(isolate, (std::string("set ", 4) + name_as_view).data());
        v8::Local<v8::FunctionTemplate> get_func =
            CreateFunctionTemplate<FunctionKind::kAttributeGet>(isolate, signature, get_name, config);
        v8::Local<v8::FunctionTemplate> set_func =
            CreateFunctionTemplate<FunctionKind::kAttributeSet>(isolate, signature, set_name, config);

        v8::Local<v8::Template> target_template;
        switch (location) {
            case FlagLocation::kInstance:
                target_template = interface_template->InstanceTemplate();
                break;
            case FlagLocation::kPrototype:
                target_template = interface_template->PrototypeTemplate();
                break;
            case FlagLocation::kInterface:
                target_template = interface_template;
                break;
            default:
                return;
        }
        target_template->SetAccessorProperty(
            name, get_func, set_func,
            static_cast<v8::PropertyAttribute>(config.v8_property_attribute));
    }


    void InstallAttributes(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs) {

        if (configs == NULL || !PyTuple_Check(configs))
            return;

        int length = PySequence_Length(configs);
        for (int i = 0; i < length; i++) {
            PyObject* config = PyTuple_GetItem(configs, i);

            if (config == NULL || !PyTuple_Check(config))
                continue;
            
            AttributeConfig v8_config;

            PyObject* attr_name = PyTuple_GetItem(config, 0);
            v8_config.name = PyUnicode_AsUTF8_abi3(attr_name);

            PyObject* type = PyTuple_GetItem(config, 3);
            int cb_type;
            if (type != NULL && PyLong_Check(type)) {
                cb_type = PyLong_AsLong(type);
            }

            PyObject* location = PyTuple_GetItem(config, 4);
            if (location != NULL && PyLong_Check(location)) {
                v8_config.location = PyLong_AsLong(location);
            }

            PyObject* cb_get_name = PyTuple_GetItem(config, 1);
            if (cb_get_name != NULL && PyUnicode_Check(cb_get_name)) {
                if (cb_type == unsigned(CallbackType::kPy)) {
                    PyObject* py_get_method = PyObject_GetAttr(cls, cb_get_name);
                    v8_config.get_method = External::New(isolate, py_get_method);
                    v8_config.callback_for_get = py_attribute_for_get;
                }
                else {
                    v8_config.get_method = External::New(isolate, cb_get_name);
                    v8_config.callback_for_get = FakerJsFunctionCallback;
                }
            }

            PyObject* cb_set_name = PyTuple_GetItem(config, 2);
            if (cb_set_name != NULL && PyUnicode_Check(cb_set_name)) {
                if (v8_config.location == unsigned(FlagLocation::kInstance) && cb_type == unsigned(CallbackType::kPy)) {
                    PyObject* py_set_method = PyObject_GetAttr(cls, cb_set_name);
                    PyObject_SetAttrString(py_set_method, "__v8_raw_name__", PyUnicode_FromString(v8_config.name));
                    v8_config.set_method = External::New(isolate, py_set_method);
                    v8_config.callback_for_set = py_attribute_for_instance_set;
                }
                else if (v8_config.location == unsigned(FlagLocation::kPrototype) && cb_type == unsigned(CallbackType::kPy)) {
                    PyObject* py_set_method = PyObject_GetAttr(cls, cb_set_name);
                    v8_config.set_method = External::New(isolate, py_set_method);
                    v8_config.callback_for_set = py_attribute_for_property_set;
                }
                else {
                    v8_config.set_method = External::New(isolate, cb_set_name);
                    v8_config.callback_for_set = FakerJsFunctionCallback;
                }
            }
            else {
                v8_config.callback_for_set = nullptr;
            }

            PyObject* property_attribute = PyTuple_GetItem(config, 5);
            if (property_attribute != NULL && PyLong_Check(property_attribute)) {
                v8_config.v8_property_attribute = PyLong_AsLong(property_attribute);
            }

            PyObject* receiver_check = PyTuple_GetItem(config, 6);
            if (receiver_check != NULL && PyLong_Check(receiver_check)) {
                v8_config.receiver_check = PyLong_AsLong(receiver_check);
            }

            PyObject* cross_origin_get_check = PyTuple_GetItem(config, 7);
            if (cross_origin_get_check != NULL && PyLong_Check(cross_origin_get_check)) {
                v8_config.cross_origin_check_for_get = PyLong_AsLong(cross_origin_get_check);
            }

            PyObject* cross_origin_set_check = PyTuple_GetItem(config, 8);
            if (cross_origin_set_check != NULL && PyLong_Check(cross_origin_set_check)) {
                v8_config.cross_origin_check_for_set = PyLong_AsLong(cross_origin_set_check);
            }

            PyObject* side_effect = PyTuple_GetItem(config, 9);
            if (side_effect != NULL && PyLong_Check(side_effect)) {
                v8_config.v8_side_effect = PyLong_AsLong(side_effect);
            }
            InstallAttribute(isolate, world, interface_template, signature, v8_config);

        }
    }

    void InstallOperation(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        const OperationConfig& config) {

        FlagLocation location = static_cast<FlagLocation>(config.location);
        if (static_cast<FlagReceiverCheck>(
            config.receiver_check) == FlagReceiverCheck::kDoNotCheck ||
            location == FlagLocation::kInterface)
            signature = v8::Local<v8::Signature>();

        v8::Local<v8::String> name = V8String(isolate, config.name);
        v8::Local<v8::FunctionTemplate> func =
            CreateFunctionTemplate<FunctionKind::kOperation>(isolate, signature, name, config);

        v8::Local<v8::Template> target_template;
        switch (location) {
        case FlagLocation::kInstance:
            target_template = interface_template->InstanceTemplate();
            break;
        case FlagLocation::kPrototype:
            target_template = interface_template->PrototypeTemplate();
            break;
        case FlagLocation::kInterface:
            target_template = interface_template;
            break;
        default:
            return;
        }
        target_template->Set(
            name, func,
            static_cast<v8::PropertyAttribute>(config.v8_property_attribute));
    }

    void InstallOperations(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs) {
        if (configs == NULL || !PyTuple_Check(configs))
            return;

        int length = PySequence_Length(configs);
        for (int i = 0; i < length; i++) {

            PyObject* config = PyTuple_GetItem(configs, i);
            if (config == NULL || !PyTuple_Check(config))
                continue;

            OperationConfig v8_config;

            PyObject* method_name = PyTuple_GetItem(config, 0);
            v8_config.name = PyUnicode_AsUTF8_abi3(method_name);


            PyObject* type = PyTuple_GetItem(config, 3);
            int cb_type;
            if (type != NULL && PyLong_Check(type)) {
                cb_type = PyLong_AsLong(type);
            }

            PyObject* location = PyTuple_GetItem(config, 4);
            if (location != NULL && PyLong_Check(location)) {
                v8_config.location = PyLong_AsLong(location);
            }

            PyObject* cb_name = PyTuple_GetItem(config, 1);
            if (cb_name != NULL && PyUnicode_Check(cb_name)) {
                if (cb_type == unsigned(CallbackType::kPy)) {
                    PyObject* py_method = PyObject_GetAttr(cls, cb_name);
                    v8_config.js_method = External::New(isolate, py_method);
                    v8_config.callback = py_class_method_callback;
                    //if(strcmp(PyUnicode_AsUTF8_abi3(cb_name), "fn_postMessage")==0 && strcmp(world, "Window") == 0)
                    //    v8_config.callback = py_class_cross_origin_method_callback;
                    //else
                    //    v8_config.callback = py_class_method_callback;
                }
                else {
                    v8_config.js_method = External::New(isolate, cb_name);
                    v8_config.callback = FakerJsFunctionCallback;
                }
            }

            PyObject* length = PyTuple_GetItem(config, 2);
            if (length != NULL && PyLong_Check(length)) {
                v8_config.length = PyLong_AsLong(length);
            }

            PyObject* property_attribute = PyTuple_GetItem(config, 5);
            if (property_attribute != NULL && PyLong_Check(property_attribute)) {
                v8_config.v8_property_attribute = PyLong_AsLong(property_attribute);
            }

            PyObject* receiver_check = PyTuple_GetItem(config, 6);
            if (receiver_check != NULL && PyLong_Check(receiver_check)) {
                v8_config.receiver_check = PyLong_AsLong(receiver_check);
            }

            PyObject* cross_origin_check = PyTuple_GetItem(config, 7);
            if (cross_origin_check != NULL && PyLong_Check(cross_origin_check)) {
                v8_config.cross_origin_check = PyLong_AsLong(cross_origin_check);
            }

            PyObject* side_effect = PyTuple_GetItem(config, 8);
            if (side_effect != NULL && PyLong_Check(side_effect)) {
                v8_config.v8_side_effect = PyLong_AsLong(side_effect);
            }

            InstallOperation(isolate, world, interface_template, signature, v8_config);
        }
    }


    void InstallConstantValue(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        ConstantValueConfig& configs) {

        const bool has_prototype_template = !interface_template->PrototypeTemplate().IsEmpty();
        const v8::PropertyAttribute v8_property_attribute =
            static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontDelete);


        v8::Local<v8::String> name = V8String(isolate, configs.name);
        v8::Local<v8::Integer> value;
        if (configs.value < 0) {
            int32_t i32_value = static_cast<int32_t>(configs.value);
            value = v8::Integer::New(isolate, i32_value);
        }
        else {
            uint32_t u32_value = static_cast<uint32_t>(configs.value);
            value = v8::Integer::NewFromUnsigned(isolate, u32_value);
        }
        if (has_prototype_template) {
            interface_template->PrototypeTemplate()->Set(name, value, v8_property_attribute);
        }
        interface_template->Set(name, value, v8_property_attribute);
    }


    void InstallConstantValues(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs) {
        if (configs == NULL)
            return;

        if (!(PyList_Check(configs) || PyTuple_Check(configs)))
            return;

        int length = PySequence_Length(configs);
        for (int i = 0; i < length; i++) {

            PyObject* config = PySequence_GetItem(configs, i);
            if (config == NULL)
                continue;

            if (!(PyList_Check(config) || PyTuple_Check(config))) {
                Py_XDECREF(config);
                continue;
            }
            ConstantValueConfig v8_config;

            PyObject* attr_name = PySequence_GetItem(config, 0);
            v8_config.name = PyUnicode_AsUTF8_abi3(attr_name);
            Py_XDECREF(attr_name);

            PyObject* attr_value = PySequence_GetItem(config, 1);
            v8_config.value = PyLong_AsLong(attr_value);
            Py_XDECREF(attr_value);

            InstallConstantValue(isolate, world, interface_template, signature, v8_config);

            Py_XDECREF(config);
        }
    }


    void InstallConstantCallbacks(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs) {
        if (configs == NULL)
            return;

        if (!(PyList_Check(configs) || PyTuple_Check(configs)))
            return;

        const bool has_prototype_template = !interface_template->PrototypeTemplate().IsEmpty();
        const v8::PropertyAttribute v8_property_attribute =
            static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontDelete);

        int length = PySequence_Length(configs);
        for (int i = 0; i < length; i++) {

            PyObject* config = PySequence_GetItem(configs, i);
            if (config == NULL)
                continue;

            if (!(PyList_Check(config) || PyTuple_Check(config))) {
                Py_XDECREF(config);
                continue;
            }
            ConstantCallbackConfig v8_config;

            PyObject* attr_name = PySequence_GetItem(config, 0);
            v8_config.name = PyUnicode_AsUTF8_abi3(attr_name);
            Py_XDECREF(attr_name);

            PyObject* attr_cb_name = PySequence_GetItem(config, 1);
            v8_config.callback_name = PyUnicode_AsUTF8_abi3(attr_cb_name);
            v8_config.callback = FakerJsFunctionAccessorNameGetterCallback;
            Py_XDECREF(attr_cb_name);

            v8::Local<v8::String> name = V8String(isolate, v8_config.name);
            if (has_prototype_template) {
                interface_template->PrototypeTemplate()->SetLazyDataProperty(
                    name, v8_config.callback, V8String(isolate, v8_config.callback_name), v8_property_attribute,
                    v8::SideEffectType::kHasNoSideEffect);
            }
            interface_template->SetLazyDataProperty(
                name, v8_config.callback, V8String(isolate, v8_config.callback_name), v8_property_attribute,
                v8::SideEffectType::kHasNoSideEffect);

            Py_XDECREF(config);
        }
    }

    void InstallExposedConstruct(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> instance_template, const char* name) {
            instance_template->SetLazyDataProperty(
                V8String(isolate, name), ConstructAccessorNameGetterCallback, v8::Local<v8::Value>(), v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect);
    }


    void InstallExposedConstruct(v8::Local<v8::Context> context, v8::Local<v8::Object> instance_object, const char* name) {
        //v8::Local<v8::Context> context = isolate->GetCurrentContext();
        instance_object
            ->SetLazyDataProperty(context, V8String(isolate, name),
                ConstructAccessorNameGetterCallback, v8::Local<v8::Value>(),
                v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect)
            .ToChecked();
    }

} // namespace bindings
