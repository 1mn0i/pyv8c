#include "v8tools.h"
#include "convert.h"

#include <climits>

using namespace v8;


namespace bindings {

    static bool RequireConfigTuple(PyObject* config, Py_ssize_t expected_size,
                                   const char* config_name) {
        if (!PyTuple_Check(config) || PyTuple_Size(config) < expected_size) {
            PyErr_Format(PyExc_TypeError,
                         "%s must be at least a %zd-item tuple",
                         config_name, expected_size);
            return false;
        }
        return true;
    }

    static bool GetConfigString(PyObject* config, Py_ssize_t index,
                                const char* field_name, bool allow_none,
                                PyObject** value, const char** text) {
        PyObject* item = PyTuple_GetItem(config, index);
        if (allow_none && item == Py_None) {
            *value = item;
            *text = nullptr;
            return true;
        }
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError, "%s must be a string%s",
                         field_name, allow_none ? " or None" : "");
            return false;
        }
        const char* utf8 = PyUnicode_AsUTF8_abi3(item);
        if (utf8 == nullptr)
            return false;
        *value = item;
        *text = utf8;
        return true;
    }

    static bool GetConfigLong(PyObject* config, Py_ssize_t index,
                              const char* field_name, long minimum,
                              long maximum, long* value) {
        PyObject* item = PyTuple_GetItem(config, index);
        if (!PyLong_Check(item)) {
            PyErr_Format(PyExc_TypeError, "%s must be an integer", field_name);
            return false;
        }
        long parsed = PyLong_AsLong(item);
        if (PyErr_Occurred())
            return false;
        if (parsed < minimum || parsed > maximum) {
            PyErr_Format(PyExc_ValueError,
                         "%s must be between %ld and %ld",
                         field_name, minimum, maximum);
            return false;
        }
        *value = parsed;
        return true;
    }

    static bool RetainCallbackData(PyObject* owner, PyObject* callback_data) {
        if (!PyList_Check(owner)) {
            PyErr_SetString(PyExc_RuntimeError,
                            "callback data owner is unavailable");
            return false;
        }
        return PyList_Append(owner, callback_data) == 0;
    }


    void FakerJsFunctionCallback(const FunctionCallbackInfo<Value>& info) {
        v8::Isolate* isolate = info.GetIsolate();
        Local<Context> context = isolate->GetCurrentContext();
        Local<Object> global = context->Global();
        Local<Object> faker = global
            ->Get(context, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker))
            .ToLocalChecked()
            .As<Object>();
        PyObject* cb_name = (PyObject*)v8_compat::external_value(info.Data());
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
        context_c* py_ctx = (context_c*)v8_compat::external_value(context->GetEmbedderData(CONTEXT_OBJECT_SLOT));
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
                Py_DECREF(name);
                js_throw_py();
                return;
            }
            if (cls != Py_None) {
                py_cls = py_class_to_template(cls, py_ctx);
                Py_DECREF(cls);
                if (py_cls == NULL) {
                    Py_DECREF(name);
                    js_throw_py();
                    return;
                }
                // py_class_to_template keys the cache by the Python class
                // __name__, while this accessor looks interfaces up by their IDL
                // name. Alias the entry when the two differ.
                PyObject* existing = PyDict_GetItem(py_ctx->class_template_cache, name);
                if (existing == NULL &&
                    PyDict_SetItem(py_ctx->class_template_cache, name, py_cls) < 0) {
                    Py_DECREF(name);
                    js_throw_py();
                    return;
                }
            }
            else {
                Py_DECREF(cls);
            }
        }
        if (py_cls == NULL) {
            Py_DECREF(name);
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


    bool InstallAttributes(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs,
        PyObject* callback_data_owner) {

        if (configs == NULL)
            return true;
        if (!PyTuple_Check(configs)) {
            PyErr_SetString(PyExc_TypeError,
                            "__v8_attribute__ must be a tuple");
            return false;
        }

        Py_ssize_t length = PyTuple_Size(configs);
        for (Py_ssize_t i = 0; i < length; i++) {
            PyObject* config = PyTuple_GetItem(configs, i);
            if (config == NULL ||
                !RequireConfigTuple(config, 10, "attribute config"))
                return false;
            
            AttributeConfig v8_config;
            PyObject* attr_name;
            PyObject* cb_get_name;
            PyObject* cb_set_name;
            const char* get_name;
            const char* set_name;
            long cb_type;
            long location;
            long property_attribute;
            long receiver_check;
            long cross_origin_get_check;
            long cross_origin_set_check;
            long side_effect;
            if (!GetConfigString(config, 0, "attribute name", false,
                                 &attr_name, &v8_config.name) ||
                !GetConfigString(config, 1, "attribute getter", false,
                                 &cb_get_name, &get_name) ||
                !GetConfigString(config, 2, "attribute setter", true,
                                 &cb_set_name, &set_name) ||
                !GetConfigLong(config, 3, "attribute callback type", 0, 1,
                               &cb_type) ||
                !GetConfigLong(config, 4, "attribute location", 0, 2,
                               &location) ||
                !GetConfigLong(config, 5, "attribute property flags", 0, 7,
                               &property_attribute) ||
                !GetConfigLong(config, 6, "attribute receiver check", 0, 1,
                               &receiver_check) ||
                !GetConfigLong(config, 7, "attribute getter cross-origin check",
                               0, 1, &cross_origin_get_check) ||
                !GetConfigLong(config, 8, "attribute setter cross-origin check",
                               0, 1, &cross_origin_set_check) ||
                !GetConfigLong(config, 9, "attribute side effect", 0, 2,
                               &side_effect))
                return false;

            v8_config.location = static_cast<unsigned>(location);
            v8_config.v8_property_attribute =
                static_cast<unsigned>(property_attribute);
            v8_config.receiver_check = static_cast<unsigned>(receiver_check);
            v8_config.cross_origin_check_for_get =
                static_cast<unsigned>(cross_origin_get_check);
            v8_config.cross_origin_check_for_set =
                static_cast<unsigned>(cross_origin_set_check);
            v8_config.v8_side_effect = static_cast<unsigned>(side_effect);

            if (cb_type == static_cast<long>(CallbackType::kPy)) {
                PyObject* py_get_method = PyObject_GetAttr(cls, cb_get_name);
                if (py_get_method == NULL)
                    return false;
                if (!RetainCallbackData(callback_data_owner, py_get_method)) {
                    Py_DECREF(py_get_method);
                    return false;
                }
                v8_config.get_method =
                    v8_compat::external_new(isolate, py_get_method);
                v8_config.callback_for_get = py_attribute_for_get;
                Py_DECREF(py_get_method);
            }
            else {
                v8_config.get_method =
                    v8_compat::external_new(isolate, cb_get_name);
                v8_config.callback_for_get = FakerJsFunctionCallback;
            }

            if (set_name != nullptr) {
                if (cb_type == static_cast<long>(CallbackType::kPy)) {
                    PyObject* py_set_method = PyObject_GetAttr(cls, cb_set_name);
                    if (py_set_method == NULL)
                        return false;
                    if (location == static_cast<long>(FlagLocation::kInstance)) {
                        PyObject* raw_name = PyUnicode_FromString(v8_config.name);
                        if (raw_name == NULL ||
                            PyObject_SetAttrString(
                                py_set_method, "__v8_raw_name__", raw_name) < 0) {
                            Py_XDECREF(raw_name);
                            Py_DECREF(py_set_method);
                            return false;
                        }
                        Py_DECREF(raw_name);
                        v8_config.callback_for_set =
                            py_attribute_for_instance_set;
                    }
                    else {
                        v8_config.callback_for_set =
                            py_attribute_for_property_set;
                    }
                    if (!RetainCallbackData(
                            callback_data_owner, py_set_method)) {
                        Py_DECREF(py_set_method);
                        return false;
                    }
                    v8_config.set_method =
                        v8_compat::external_new(isolate, py_set_method);
                    Py_DECREF(py_set_method);
                }
                else {
                    v8_config.set_method =
                        v8_compat::external_new(isolate, cb_set_name);
                    v8_config.callback_for_set = FakerJsFunctionCallback;
                }
            }
            InstallAttribute(isolate, world, interface_template, signature, v8_config);
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

    bool InstallOperations(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* cls,
        PyObject* configs,
        PyObject* callback_data_owner) {
        if (configs == NULL)
            return true;
        if (!PyTuple_Check(configs)) {
            PyErr_SetString(PyExc_TypeError,
                            "__v8_method__ must be a tuple");
            return false;
        }

        Py_ssize_t length = PyTuple_Size(configs);
        for (Py_ssize_t i = 0; i < length; i++) {

            PyObject* config = PyTuple_GetItem(configs, i);
            if (config == NULL ||
                !RequireConfigTuple(config, 9, "operation config"))
                return false;

            OperationConfig v8_config;
            PyObject* method_name;
            PyObject* cb_name;
            const char* callback_name;
            long function_length;
            long cb_type;
            long location;
            long property_attribute;
            long receiver_check;
            long cross_origin_check;
            long side_effect;
            if (!GetConfigString(config, 0, "operation name", false,
                                 &method_name, &v8_config.name) ||
                !GetConfigString(config, 1, "operation callback", false,
                                 &cb_name, &callback_name) ||
                !GetConfigLong(config, 2, "operation length", 0, 255,
                               &function_length) ||
                !GetConfigLong(config, 3, "operation callback type", 0, 1,
                               &cb_type) ||
                !GetConfigLong(config, 4, "operation location", 0, 2,
                               &location) ||
                !GetConfigLong(config, 5, "operation property flags", 0, 7,
                               &property_attribute) ||
                !GetConfigLong(config, 6, "operation receiver check", 0, 1,
                               &receiver_check) ||
                !GetConfigLong(config, 7, "operation cross-origin check", 0, 1,
                               &cross_origin_check) ||
                !GetConfigLong(config, 8, "operation side effect", 0, 2,
                               &side_effect))
                return false;

            v8_config.length = static_cast<unsigned>(function_length);
            v8_config.location = static_cast<unsigned>(location);
            v8_config.v8_property_attribute =
                static_cast<unsigned>(property_attribute);
            v8_config.receiver_check = static_cast<unsigned>(receiver_check);
            v8_config.cross_origin_check =
                static_cast<unsigned>(cross_origin_check);
            v8_config.v8_side_effect = static_cast<unsigned>(side_effect);

            if (cb_type == static_cast<long>(CallbackType::kPy)) {
                PyObject* py_method = PyObject_GetAttr(cls, cb_name);
                if (py_method == NULL)
                    return false;
                PyObject* callback_data = PyTuple_Pack(
                    2, py_method, method_name);
                Py_DECREF(py_method);
                if (callback_data == NULL)
                    return false;
                if (!RetainCallbackData(
                        callback_data_owner, callback_data)) {
                    Py_DECREF(callback_data);
                    return false;
                }
                v8_config.callback_data =
                    v8_compat::external_new(isolate, callback_data);
                Py_DECREF(callback_data);
                v8_config.callback = py_class_method_callback;
            }
            else {
                v8_config.callback_data =
                    v8_compat::external_new(isolate, cb_name);
                v8_config.callback = FakerJsFunctionCallback;
            }

            InstallOperation(isolate, world, interface_template, signature, v8_config);
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


    bool InstallConstantValues(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs) {
        if (configs == NULL)
            return true;

        if (!(PyList_Check(configs) || PyTuple_Check(configs))) {
            PyErr_SetString(PyExc_TypeError, "constant values must be a list or tuple");
            return false;
        }

        Py_ssize_t length = PySequence_Length(configs);
        if (length < 0)
            return false;
        for (Py_ssize_t i = 0; i < length; i++) {

            PyObject* config = PySequence_GetItem(configs, i);
            if (config == NULL)
                return false;

            if (!(PyList_Check(config) || PyTuple_Check(config)) ||
                PySequence_Size(config) != 2) {
                Py_DECREF(config);
                PyErr_Format(PyExc_TypeError,
                             "constant value entry %zd must contain exactly 2 items", i);
                return false;
            }
            ConstantValueConfig v8_config;

            PyObject* attr_name = PySequence_GetItem(config, 0);
            PyObject* attr_value = PySequence_GetItem(config, 1);
            if (attr_name == NULL || attr_value == NULL || !PyUnicode_Check(attr_name) ||
                !PyLong_Check(attr_value)) {
                Py_XDECREF(attr_name);
                Py_XDECREF(attr_value);
                Py_DECREF(config);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_TypeError,
                                    "constant value entry must be (str, int)");
                return false;
            }
            v8_config.name = PyUnicode_AsUTF8_abi3(attr_name);
            v8_config.value = PyLong_AsLongLong(attr_value);
            if (v8_config.name == nullptr || PyErr_Occurred()) {
                Py_DECREF(attr_name);
                Py_DECREF(attr_value);
                Py_DECREF(config);
                return false;
            }
            if (v8_config.value < INT32_MIN || v8_config.value > UINT32_MAX) {
                Py_DECREF(attr_name);
                Py_DECREF(attr_value);
                Py_DECREF(config);
                PyErr_Format(PyExc_OverflowError,
                             "constant value entry %zd is outside the V8 Integer range", i);
                return false;
            }

            InstallConstantValue(isolate, world, interface_template, signature, v8_config);

            Py_DECREF(attr_name);
            Py_DECREF(attr_value);
            Py_DECREF(config);
        }
        return true;
    }


    bool InstallConstantCallbacks(v8::Isolate* isolate,
        const char* world,
        v8::Local<v8::FunctionTemplate> interface_template,
        v8::Local<v8::Signature> signature,
        PyObject* configs) {
        if (configs == NULL)
            return true;

        if (!(PyList_Check(configs) || PyTuple_Check(configs))) {
            PyErr_SetString(PyExc_TypeError, "constant callbacks must be a list or tuple");
            return false;
        }

        const bool has_prototype_template = !interface_template->PrototypeTemplate().IsEmpty();
        const v8::PropertyAttribute v8_property_attribute =
            static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontDelete);

        Py_ssize_t length = PySequence_Length(configs);
        if (length < 0)
            return false;
        for (Py_ssize_t i = 0; i < length; i++) {

            PyObject* config = PySequence_GetItem(configs, i);
            if (config == NULL)
                return false;

            if (!(PyList_Check(config) || PyTuple_Check(config)) ||
                PySequence_Size(config) != 2) {
                Py_DECREF(config);
                PyErr_Format(PyExc_TypeError,
                             "constant callback entry %zd must contain exactly 2 items", i);
                return false;
            }
            ConstantCallbackConfig v8_config;

            PyObject* attr_name = PySequence_GetItem(config, 0);
            PyObject* attr_cb_name = PySequence_GetItem(config, 1);
            if (attr_name == NULL || attr_cb_name == NULL || !PyUnicode_Check(attr_name) ||
                !PyUnicode_Check(attr_cb_name)) {
                Py_XDECREF(attr_name);
                Py_XDECREF(attr_cb_name);
                Py_DECREF(config);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_TypeError,
                                    "constant callback entry must be (str, str)");
                return false;
            }
            v8_config.name = PyUnicode_AsUTF8_abi3(attr_name);
            v8_config.callback_name = PyUnicode_AsUTF8_abi3(attr_cb_name);
            v8_config.callback = FakerJsFunctionAccessorNameGetterCallback;
            if (v8_config.name == nullptr || v8_config.callback_name == nullptr) {
                Py_DECREF(attr_name);
                Py_DECREF(attr_cb_name);
                Py_DECREF(config);
                return false;
            }

            v8::Local<v8::String> name = V8String(isolate, v8_config.name);
            if (has_prototype_template) {
                interface_template->PrototypeTemplate()->SetLazyDataProperty(
                    name, v8_config.callback, V8String(isolate, v8_config.callback_name), v8_property_attribute,
                    v8::SideEffectType::kHasNoSideEffect);
            }
            interface_template->SetLazyDataProperty(
                name, v8_config.callback, V8String(isolate, v8_config.callback_name), v8_property_attribute,
                v8::SideEffectType::kHasNoSideEffect);

            Py_DECREF(attr_name);
            Py_DECREF(attr_cb_name);
            Py_DECREF(config);
        }
        return true;
    }

    void InstallExposedConstruct(v8::Isolate* isolate, v8::Local<v8::ObjectTemplate> instance_template, const char* name) {
            instance_template->SetLazyDataProperty(
                V8String(isolate, name), ConstructAccessorNameGetterCallback, v8::Local<v8::Value>(), v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect);
    }


    void InstallExposedConstruct(v8::Local<v8::Context> context, v8::Local<v8::Object> instance_object, const char* name) {
        v8::Isolate* isolate = v8_compat::context_isolate(context);
        instance_object
            ->SetLazyDataProperty(context, V8String(isolate, name),
                ConstructAccessorNameGetterCallback, v8::Local<v8::Value>(),
                v8::DontEnum,
                v8::SideEffectType::kHasNoSideEffect)
            .ToChecked();
    }

} // namespace bindings
