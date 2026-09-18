#include "v8tools.h"
#include "idl_spec.h"
#include "convert.h"
#include "faker/trace.h"
#include "pyclass_handlers_internal.h"

#include <climits>

using namespace v8;


namespace bindings {

    static bool RetainCallbackData(PyObject* owner, PyObject* callback_data) {
        return PyList_Append(owner, callback_data) == 0;
    }


    static void FakerJsFunctionImpl(const FunctionCallbackInfo<Value>& info, PyObject* cb_name) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        Local<Object> global = context->Global();
        Local<Value> faker_value;
        if (!global->Get(context, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker)).ToLocal(&faker_value)) return;
        if (!faker_value->IsObject()) {
            isolate->ThrowException(Exception::TypeError(V8String(isolate, "binding dispatcher is not an object")));
            return;
        }
        Local<Object> faker = faker_value.As<Object>();

        Local<String> cb = V8String(isolate, PyUnicode_AsUTF8_abi3(cb_name));
        Local<Value> func;
        if (!faker->Get(context, cb).ToLocal(&func)) return;
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
        Local<Value> returned;
        if (result.ToLocal(&returned)) info.GetReturnValue().Set(returned);
    }

    template<int kind>
    static void FakerJsBindingCallback(const FunctionCallbackInfo<Value>& info) {
        if (!context_check_callback(info.GetIsolate()->GetCurrentContext())) return;
        PyObject* data = IsolateState::callback_target(info.Data());
        if (data == nullptr) { js_throw_py(); return; }
        PyObject* name;
        PyObject* callback;
        if constexpr (kind == 2) {
            auto* binding = (MethodSpec*)data;
            name = binding->name;
            callback = binding->callback;
        } else {
            auto* binding = (AttributeSpec*)data;
            name = binding->name;
            callback = kind == 0 ? binding->getter : binding->setter;
        }
        auto* owner = context_from_v8(info.GetIsolate()->GetCurrentContext());
        if (!context_trace_enabled(owner)) { FakerJsFunctionImpl(info, callback); return; }
        faker::TraceScope observation(info, kind, name, get_self(info), owner);
        FakerJsFunctionImpl(info, callback);
    }

    void FakerJsFunctionAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        Local<Object> global = context->Global();
        Local<Object> faker = global
            ->Get(context, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker))
            .ToLocalChecked()
            .As<Object>();
        Local<String> cb = info.Data().As<String>();
        Local<Value> func = faker->Get(context, cb).ToLocalChecked();
        if (func.IsEmpty() || !func->IsFunction()) {
            info.GetIsolate()->ThrowException(v8::Exception::Error(String::Concat(isolate, V8String(isolate, "patch callback "), cb)));
            return;
        }

        TryCatch try_catch(isolate);
        MaybeLocal<Value> result = func.As<Function>()->Call(
            context, v8_compat::property_callback_receiver(info), 0, nullptr);

        if (try_catch.HasCaught()) {
            try_catch.ReThrow();
            return;
        }
        info.GetReturnValue().Set(result.ToLocalChecked());
    }

    void ConstructAccessorNameGetterCallback(Local<Name> property, const PropertyCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        if (!context_check_callback(context)) return;
        context_c* py_ctx = context_from_v8(context);
        if (py_ctx == nullptr) {
            info.GetReturnValue().SetUndefined();
            return;
        }

        String::Utf8Value property_name(isolate, property);
        PyObject* name = PyUnicode_FromString(*property_name);
        if (name == NULL) {
            js_throw_py();
            return;
        }
        PyObject* py_cls = PyDict_GetItem(py_ctx->class_template_cache, name);
        if (py_cls == NULL && py_ctx->lazy_factory != NULL) {
            // Lazy materialisation: ask Python for the class behind this name,
            // build its template and remember it so later accesses resolve from
            // the cache without another Python round trip.
            PyObject* cls = PyObject_CallFunctionObjArgs(py_ctx->lazy_factory, name, NULL);
            if (cls == NULL) {
                Py_DECREF((PyObject*)name);
                js_throw_py();
                return;
            }
            if (cls != Py_None) {
                if (PySet_Add(py_ctx->resolving_classes, cls) < 0) {
                    Py_DECREF((PyObject*)cls);
                    Py_DECREF((PyObject*)name);
                    js_throw_py();
                    return;
                }
                py_cls = py_class_to_template(cls, py_ctx);
                PySet_Discard(py_ctx->resolving_classes, cls);
                Py_DECREF((PyObject*)cls);
                if (py_cls == NULL) {
                    Py_DECREF((PyObject*)name);
                    js_throw_py();
                    return;
                }
                // py_class_to_template keys the cache by the Python class
                // __name__, while this accessor looks interfaces up by their IDL
                // name. Alias the entry when the two differ.
                PyObject* existing = PyDict_GetItem(py_ctx->class_template_cache, name);
                if (existing == NULL &&
                    PyDict_SetItem(py_ctx->class_template_cache, name, py_cls) < 0) {
                    Py_DECREF((PyObject*)name);
                    js_throw_py();
                    return;
                }
            }
            else {
                Py_DECREF((PyObject*)cls);
            }
        }
        if (py_cls == NULL) {
            Py_DECREF((PyObject*)name);
            info.GetReturnValue().SetUndefined();
            return;
        }
        Local<FunctionTemplate> interface_template = ((py_class*)py_cls)->templ.Get(isolate);
        Py_XDECREF((PyObject*)name);
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
        return config.callback_data;
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
            v8_compat::c_function_overloads(
                v8_cfunction_table_data, v8_cfunction_table_size));

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


    bool InstallAttributes(v8::Isolate* isolate, const char* world,
        v8::Local<v8::FunctionTemplate> interface_template, v8::Local<v8::Signature> signature,
        PyObject* cls, PyObject* configs, PyObject* callback_data_owner, context_c* owner) {
        if (configs == nullptr) return true;
        for (Py_ssize_t index = 0; index < PyTuple_Size(configs); ++index) {
            PyObject* resolved = idl_spec_resolve(PyTuple_GetItem(configs, index), cls);
            if (resolved == nullptr) return false;
            if (!RetainCallbackData(callback_data_owner, resolved)) { Py_DECREF((PyObject*)resolved); return false; }
            auto* spec = (AttributeSpec*)resolved;
            AttributeConfig config;
            config.name = PyUnicode_AsUTF8_abi3(spec->name);
            config.location = (unsigned)spec->location;
            config.v8_property_attribute = (unsigned)spec->property_flags;
            config.receiver_check = (unsigned)spec->receiver_check;
            config.cross_origin_check_for_get = (unsigned)spec->cross_origin_get_check;
            config.cross_origin_check_for_set = (unsigned)spec->cross_origin_set_check;
            config.v8_side_effect = (unsigned)spec->side_effect;
            config.get_method = owner->isolate_state->callback_data(owner, resolved);
            if (config.get_method.IsEmpty()) { Py_DECREF(resolved); return false; }
            config.callback_for_get = spec->callback_type == 0 ? CallbackBoundary<py_attribute_for_get>::call : CallbackBoundary<FakerJsBindingCallback<0>>::call;
            if (spec->setter != nullptr) {
                config.set_method = config.get_method;
                config.callback_for_set = spec->callback_type == 0 ? (spec->location == 0 ? CallbackBoundary<py_attribute_for_instance_set>::call : CallbackBoundary<py_attribute_for_property_set>::call) : CallbackBoundary<FakerJsBindingCallback<1>>::call;
            }
            bool valid = config.name != nullptr;
            if (valid) InstallAttribute(isolate, world, interface_template, signature, config);
            Py_DECREF((PyObject*)resolved);
            if (!valid) return false;
        }
        return true;
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

    bool InstallOperations(v8::Isolate* isolate, const char* world,
        v8::Local<v8::FunctionTemplate> interface_template, v8::Local<v8::Signature> signature,
        PyObject* cls, PyObject* configs, PyObject* callback_data_owner, context_c* owner) {
        if (configs == nullptr) return true;
        for (Py_ssize_t index = 0; index < PyTuple_Size(configs); ++index) {
            PyObject* resolved = idl_spec_resolve(PyTuple_GetItem(configs, index), cls);
            if (resolved == nullptr) return false;
            if (!RetainCallbackData(callback_data_owner, resolved)) { Py_DECREF((PyObject*)resolved); return false; }
            auto* spec = (MethodSpec*)resolved;
            OperationConfig config;
            config.name = PyUnicode_AsUTF8_abi3(spec->name);
            config.length = (unsigned)spec->length;
            config.location = (unsigned)spec->location;
            config.v8_property_attribute = (unsigned)spec->property_flags;
            config.receiver_check = (unsigned)spec->receiver_check;
            config.cross_origin_check = (unsigned)spec->cross_origin_check;
            config.v8_side_effect = (unsigned)spec->side_effect;
            config.callback_data = owner->isolate_state->callback_data(owner, resolved);
            if (config.callback_data.IsEmpty()) { Py_DECREF(resolved); return false; }
            config.callback = spec->callback_type == 0 ? CallbackBoundary<py_class_method_callback>::call : CallbackBoundary<FakerJsBindingCallback<2>>::call;
            bool valid = config.name != nullptr;
            if (valid) InstallOperation(isolate, world, interface_template, signature, config);
            Py_DECREF((PyObject*)resolved);
            if (!valid) return false;
        }
        return true;
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


    bool InstallConstantValues(v8::Isolate* isolate, const char* world,
        v8::Local<v8::FunctionTemplate> interface_template, v8::Local<v8::Signature> signature, PyObject* configs) {
        if (configs == nullptr) return true;
        for (Py_ssize_t index = 0; index < PyTuple_Size(configs); ++index) {
            auto* spec = (ConstantSpec*)PyTuple_GetItem(configs, index);
            ConstantValueConfig config;
            config.name = PyUnicode_AsUTF8_abi3(spec->name);
            config.value = spec->value;
            if (config.name == nullptr) return false;
            InstallConstantValue(isolate, world, interface_template, signature, config);
        }
        return true;
    }

    bool InstallConstantCallbacks(v8::Isolate* isolate, const char* world,
        v8::Local<v8::FunctionTemplate> interface_template, v8::Local<v8::Signature> signature, PyObject* configs) {
        if (configs == nullptr) return true;
        for (Py_ssize_t index = 0; index < PyTuple_Size(configs); ++index) {
            auto* spec = (ConstantCallbackSpec*)PyTuple_GetItem(configs, index);
            const char* name = PyUnicode_AsUTF8_abi3(spec->name);
            const char* callback = PyUnicode_AsUTF8_abi3(spec->callback);
            if (name == nullptr || callback == nullptr) return false;
            auto flags = static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontDelete);
            if (!interface_template->PrototypeTemplate().IsEmpty()) interface_template->PrototypeTemplate()->SetLazyDataProperty(V8String(isolate, name), CallbackBoundary<FakerJsFunctionAccessorNameGetterCallback>::call, V8String(isolate, callback), flags, v8::SideEffectType::kHasNoSideEffect);
            interface_template->SetLazyDataProperty(V8String(isolate, name), CallbackBoundary<FakerJsFunctionAccessorNameGetterCallback>::call, V8String(isolate, callback), flags, v8::SideEffectType::kHasNoSideEffect);
        }
        return true;
    }

    void InstallExposedConstruct(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> instance_template, const char* name) {
            instance_template->SetLazyDataProperty(
                V8String(isolate, name), CallbackBoundary<ConstructAccessorNameGetterCallback>::call, v8::Local<v8::Value>(), v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect);
    }


    void InstallExposedConstruct(v8::Local<v8::Context> context, v8::Local<v8::Object> instance_object, const char* name) {
        v8::Isolate* isolate = v8_compat::context_isolate(context);
        instance_object
            ->SetLazyDataProperty(context, V8String(isolate, name),
                CallbackBoundary<ConstructAccessorNameGetterCallback>::call, v8::Local<v8::Value>(),
                v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect)
            .ToChecked();
    }

} // namespace bindings
