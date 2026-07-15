

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FAKER_FAKER_HOOK_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FAKER_FAKER_HOOK_H_


#include <v8.h>
#include "pyv8.h"
#define HOOKHANDLE "faker"

namespace faker {

#define FAKER_RECORD                                                                    \
        ghook_key = v8::Private::ForApi(                                                   \
            isolate, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Hook));    \
        v8::MaybeLocal<v8::Value> maybe_value = ctx->Global()->GetPrivate(ctx, ghook_key);  \
        if (!maybe_value.IsEmpty()) {                                                       \
            ghook = maybe_value.ToLocalChecked()->IsTrue();                                 \
        }                                                                                   \
        ctx->Global()->SetPrivate(ctx, ghook_key, v8::Boolean::New(isolate, false));        \

#define FAKER_RECOVER                                                               \
        global->SetPrivate(ctx, ghook_key, v8::Boolean::New(isolate, ghook));

#define FAKER_DEFINE_HOOK             \
        v8::Local<v8::Private> ghook_key;



#define FAKER_INIT                                            \
    ctx = info.Holder()->GetCreationContextChecked();         \
    v8::Isolate* isolate = info.GetIsolate();                 \
    exception = v8::Undefined(isolate).As<v8::Value>();       \
    FAKER_RECORD                                              


#define FAKER_DEFINE                   \
    v8::Local<v8::Context> ctx;        \
    v8::TryCatch try_catch;            \
    v8::Local<v8::Value> exception;    \
    bool has_caught = false;           \
    bool ghook = false;                \
    context_c* ctx_c;         \
    PyObject* self = NULL;             \
    FAKER_DEFINE_HOOK


#define FAKER_CHECK_EXCEPTION_FUNCTION                      \
    void CheckException1() {                                \
        if (try_catch.HasCaught()) {                        \
            has_caught = true;                              \
            exception = try_catch.Exception();              \
            try_catch.Reset();                              \
        }                                                   \
    }                                                       \
                                                            \
    void CheckException2() {                                \
        if (has_caught) {                                   \
            info.GetIsolate()->ThrowException(exception);   \
            if (try_catch.HasCaught()) {                    \
                try_catch.ReThrow();                        \
            }                                               \
        }                                                   \
    }


#define FAKER_ARGS_RESULT_FUNCTION                                    \
    v8::Local<v8::Array> GetArgsArray() {                             \
        v8::Local<v8::Array> args =                                   \
            v8::Array::New(info.GetIsolate(), info.Length());         \
        for (int i = 0; i < info.Length(); i++) {                     \
            v8::Maybe<bool> ok = args->Set(ctx, i, info[i]);          \
            if (ok.IsJust()) {                                        \
                continue;                                             \
            }                                                         \
        }                                                             \
        return args;                                                  \
    }                                                                 \
                                                                      \
    v8::Local<v8::Value> GetResultValue() {                           \
        v8::Local<v8::Value> result = info.GetReturnValue().Get();    \
        if (has_caught) {                                             \
            result = exception;                                       \
        }                                                             \
        else if (result.IsEmpty()) {                                  \
            result = Undefined(info.GetIsolate()).As<v8::Value>();    \
        }                                                             \
        return result;                                                \
    }


class FakerHookGetter {
    public:
        FakerHookGetter() = delete;
        FakerHookGetter(const FakerHookGetter&) = delete;

        FakerHookGetter(
            const v8::PropertyCallbackInfo<v8::Value>& info,
            v8::Local<v8::Value> v8_property_name,
            PyObject* self,
            context_c* ctx_c
        ) : info{ info },
            v8_property_name(v8_property_name),
            self{ self },
            try_catch{ info.GetIsolate() },
            ctx_c{ ctx_c }
        {
            FAKER_INIT
        }

        FakerHookGetter(
            const v8::PropertyCallbackInfo<v8::Value>& info,
            uint32_t index,
            PyObject* self,
            context_c* ctx_c
        ) : info{ info },
            v8_property_name(v8::Number::New(info.GetIsolate(), index)),
            self{ self },
            try_catch{ info.GetIsolate() },
            ctx_c{ ctx_c }
        {
            FAKER_INIT
        }

        ~FakerHookGetter() {

            CheckException1();

            v8::Isolate* isolate = info.GetIsolate();
            v8::Local<v8::Object> global = ctx->Global();

            if (ghook && !global.IsEmpty()) {
                EndCall();
            }

            FAKER_RECOVER

            CheckException2();
        }

        FAKER_CHECK_EXCEPTION_FUNCTION

        void EndCall() {
            v8::Isolate* isolate = info.GetIsolate();
            v8::Local<v8::Value> property_value;
            v8::Local<v8::String> ctx_type = ctx->GetEmbedderData(CTX_TYPE).As<String>();
            String::Utf8Value ctx_type_char(isolate, ctx_type);

            if(PyObject_HasAttrString(self, "__v8_get_hook__")) {
                property_value = info.Holder()->Get(ctx, v8_property_name).FromMaybe(v8::Undefined(isolate).As<v8::Value>());
                PyObject* key = py_from_js(v8_property_name, ctx);
                PyObject* value = py_from_js(property_value, ctx);
                PyObject* exc = py_from_js(exception, ctx);
                PyObject* py_ctx_type = PyUnicode_FromString(*ctx_type_char);
                PyObject* hook_result = PyObject_CallMethod(
                    self, "__v8_get_hook__", "OOOO", key, value, exc, py_ctx_type);
                Py_XDECREF(hook_result);
                Py_XDECREF(key);
                Py_XDECREF(value);
                Py_XDECREF(exc);
                Py_XDECREF(py_ctx_type);
            }

            if (PyErr_Occurred()) return;
            if (!ctx_c->has_debugger) return;

            v8::Local<v8::Object> global = ctx->Global();
            v8::MaybeLocal<v8::Value> maybe_faker, maybe_getter;
            v8::Local<v8::Value> faker, getter;
            maybe_faker = global->Get(
                ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker));
            if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
                maybe_getter = faker.As<v8::Object>()->Get(
                    ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Getter));
                if (maybe_getter.ToLocal(&getter) && getter->IsFunction()) {
                    if (property_value.IsEmpty()) {
                        property_value = info.Holder()->Get(ctx, v8_property_name).FromMaybe(v8::Undefined(isolate).As<v8::Value>());
                    }
                    v8::Local<v8::Value> params[] = { info.Holder(), v8_property_name, property_value, exception };
                    v8::MaybeLocal<v8::Value> return_value = getter.As<v8::Function>()->Call(ctx, faker, 4, params);
                    if (return_value.IsEmpty()) {}
                }
            }
        }

    protected:
        const v8::PropertyCallbackInfo<v8::Value>& info;
        v8::Local<v8::Value> v8_property_name;
        FAKER_DEFINE
};


class FakerHookSetter {
    public:
        FakerHookSetter() = delete;
        FakerHookSetter(const FakerHookSetter&) = delete;

        FakerHookSetter(
            const v8::PropertyCallbackInfo<void>& info,
            v8::Local<v8::Value> v8_property_name,
            v8::Local<v8::Value> v8_property_value,
            PyObject* self,
            context_c* ctx_c
        ) : info{ info },
            v8_property_name{ v8_property_name },
            v8_property_value{ v8_property_value },
            self{ self },
            try_catch{ info.GetIsolate() },
            ctx_c{ ctx_c }
        {
            FAKER_INIT
        }

        FakerHookSetter(
            const v8::PropertyCallbackInfo<void>& info,
            uint32_t index,
            v8::Local<v8::Value> v8_property_value,
            PyObject* self,
            context_c* ctx_c
        ) : info{ info },
            v8_property_name(v8::Number::New(info.GetIsolate(), index)),
            v8_property_value{ v8_property_value },
            self{ self },
            try_catch{ info.GetIsolate() },
            ctx_c{ ctx_c }
        {
            FAKER_INIT
        }

        ~FakerHookSetter() {

            CheckException1();

            v8::Isolate* isolate = info.GetIsolate();
            v8::Local<v8::Object> global = ctx->Global();

            if (ghook && !global.IsEmpty()) {
                EndCall();
            }

            FAKER_RECOVER

            CheckException2();
        }

        FAKER_CHECK_EXCEPTION_FUNCTION

        void EndCall() {
            v8::Isolate* isolate = info.GetIsolate();
            v8::Local<v8::String> ctx_type = ctx->GetEmbedderData(CTX_TYPE).As<String>();
            String::Utf8Value ctx_type_char(isolate, ctx_type);

            if (PyObject_HasAttrString(self, "__v8_set_hook__")) {
                PyObject* key = py_from_js(v8_property_name, ctx);
                PyObject* value = py_from_js(v8_property_value, ctx);
                PyObject* exc = py_from_js(exception, ctx);
                PyObject* py_ctx_type = PyUnicode_FromString(*ctx_type_char);
                PyObject* hook_result = PyObject_CallMethod(
                    self, "__v8_set_hook__", "OOOO", key, value, exc, py_ctx_type);
                Py_XDECREF(hook_result);
                //PyObject_CallMethod(self, "__v8_set_hook__", "OOO", key, value, exc);
                Py_XDECREF(key);
                Py_XDECREF(value);
                Py_XDECREF(exc);
                Py_XDECREF(py_ctx_type);
            }

            if (PyErr_Occurred()) return;
            if (!ctx_c->has_debugger) return;
            
            v8::Local<v8::Object> global = ctx->Global();
            v8::MaybeLocal<v8::Value> maybe_faker, maybe_setter;
            v8::Local<v8::Value> faker, setter;
            maybe_faker = global->Get(
                ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker));
            if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
                maybe_setter = faker.As<v8::Object>()->Get(
                    ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Setter));
                if (maybe_setter.ToLocal(&setter) && setter->IsFunction()) {
                    v8::Local<v8::Value> params[] = { info.Holder(), v8_property_name, v8_property_value, exception };
                    v8::MaybeLocal<v8::Value> return_value = setter.As<v8::Function>()->Call(ctx, faker, 4, params);
                    if (return_value.IsEmpty()) {}
                }
            }
        }

    private:
        const v8::PropertyCallbackInfo<void>& info;
        v8::Local<v8::Value> v8_property_name;
        v8::Local<v8::Value> v8_property_value;
        FAKER_DEFINE
    };


class FakerHookOperation {
    public:
        FakerHookOperation() = delete;
        FakerHookOperation(const FakerHookOperation&) = delete;

        FakerHookOperation(
            const v8::FunctionCallbackInfo<v8::Value>& info,
            const char* func_name,
            PyObject* self,
            context_c* ctx_c
        ) : info{ info }, func_name(func_name), self{ self }, try_catch{ info.GetIsolate() }, ctx_c{ ctx_c }
        {
            v8::Isolate* isolate = info.GetIsolate();
            ctx = isolate->GetCurrentContext();
            exception = v8::Undefined(isolate).As<v8::Value>();
        }

        ~FakerHookOperation() {

            CheckException1();

            v8::Isolate* isolate = info.GetIsolate();
            v8::Local<v8::Object> global = ctx->Global();

            FAKER_RECORD

            EndCall();

            FAKER_RECOVER

            CheckException2();

        }

        FAKER_CHECK_EXCEPTION_FUNCTION

        FAKER_ARGS_RESULT_FUNCTION


        void EndCall() {
            if (ghook) {
                v8::Isolate* isolate = info.GetIsolate();
                v8::Local<v8::Array> args;
                v8::Local<v8::Value> ret;
                v8::Local<v8::String> ctx_type = ctx->GetEmbedderData(CTX_TYPE).As<String>();
                String::Utf8Value ctx_type_char(isolate, ctx_type);
                if (PyObject_HasAttrString(self, "__v8_method_hook__")) {
                    args = GetArgsArray();
                    ret = GetResultValue();
                    PyObject* name = PyUnicode_FromString(func_name);
                    PyObject* py_args = py_from_js(args, ctx);
                    PyObject* py_ret = py_from_js(ret, ctx);
                    PyObject* has_error = PyBool_FromLong(int(has_caught));

                    PyObject* py_ctx_type = PyUnicode_FromString(*ctx_type_char);
                    PyObject* hook_result;
                    if (PyType_Check(self)) {
                        PyObject* method = PyObject_GetAttrString(self, "__v8_method_hook__");
                        hook_result = PyObject_CallFunction(
                            method, "OOOOOO", self, name, py_args, py_ret,
                            has_error, py_ctx_type);
                        //PyObject_CallFunction(method, "OOOOO", self, name, py_args, py_ret, has_error);
                        Py_XDECREF(method);
                    }
                    else {
                        hook_result = PyObject_CallMethod(
                            self, "__v8_method_hook__", "OOOOO", name, py_args,
                            py_ret, has_error, py_ctx_type);
                        //PyObject_CallMethod(self, "__v8_method_hook__", "OOOO", name, py_args, py_ret, has_error);
                    }
                    Py_XDECREF(hook_result);

                    Py_XDECREF(name);
                    Py_XDECREF(py_args);
                    Py_XDECREF(py_ret);
                    Py_XDECREF(has_error);
                    Py_XDECREF(py_ctx_type);
                }

                if (PyErr_Occurred()) return;
                if (!ctx_c->has_debugger) return;

                v8::Local<v8::Object> global = ctx->Global();
                v8::MaybeLocal<v8::Value> maybe_faker, maybe_operation;
                v8::Local<v8::Value> faker, operation;
                maybe_faker = global->Get(
                    ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker));
                if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
                    maybe_operation = faker.As<v8::Object>()->Get(
                        ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Method));
                    if (maybe_operation.ToLocal(&operation) && operation->IsFunction()) {
                        if (args.IsEmpty()) {
                            args = GetArgsArray();
                        }
                        if (ret.IsEmpty()) {
                            ret = GetResultValue();
                        }
                        v8::Local<v8::Value> params[] = {
                            info.This(),
                            V8String(isolate, func_name),
                            args,
                            ret,
                            v8::Boolean::New(isolate, has_caught)
                        };
                        v8::MaybeLocal<v8::Value> return_value = operation.As<v8::Function>()->Call(
                                ctx, faker, 5, params);
                        if (return_value.IsEmpty()) {
                        }
                    }
                }
            }
        }

    protected:
        const v8::FunctionCallbackInfo<v8::Value>& info;
        const char* func_name;
        FAKER_DEFINE
};


class FakerHookConstruct {
public:
    FakerHookConstruct() = delete;
    FakerHookConstruct(const FakerHookConstruct&) = delete;

    FakerHookConstruct(
        const v8::FunctionCallbackInfo<v8::Value>& info,
        const char* func_name,
        PyObject* self,
        context_c* ctx_c
    ) : info{ info }, func_name{ func_name }, self{ self }, try_catch{ info.GetIsolate() }, ctx_c{ ctx_c } {}

    ~FakerHookConstruct() {

        v8::Isolate* isolate = info.GetIsolate();
        ctx = isolate->GetCurrentContext();
        v8::Local<v8::Object> global = ctx->Global();

        CheckException1();

        FAKER_RECORD

        EndCall();

        FAKER_RECOVER

       CheckException2();
    }

public:

    FAKER_CHECK_EXCEPTION_FUNCTION

    FAKER_ARGS_RESULT_FUNCTION

    void EndCall() {
        if (ghook) {
            v8::Isolate* isolate = info.GetIsolate();

            v8::Local<v8::String> ctx_type = ctx->GetEmbedderData(CTX_TYPE).As<String>();
            String::Utf8Value ctx_type_char(isolate, ctx_type);

            v8::Local<v8::Array> args;
            v8::Local<v8::Value> ret;
            if (PyObject_HasAttrString(self, "__v8_construct_hook__")) {
                args = GetArgsArray();
                ret = GetResultValue();
                PyObject* name = PyUnicode_FromString(func_name);
                PyObject* py_args = py_from_js(args, ctx);
                PyObject* py_ret = py_from_js(ret, ctx);
                PyObject* has_error = PyBool_FromLong(int(has_caught));
                PyObject* is_construct_call = PyBool_FromLong(int(info.IsConstructCall()));
                PyObject* py_ctx_type = PyUnicode_FromString(*ctx_type_char);
                PyObject* hook_result = PyObject_CallMethod(
                    self, "__v8_construct_hook__", "OOOOOO", name, py_args,
                    py_ret, has_error, is_construct_call, py_ctx_type);
                Py_XDECREF(hook_result);
                //PyObject_CallMethod(self, "__v8_construct_hook__", "OOOOOO", name, py_args, py_ret, has_error, is_construct_call);
                Py_XDECREF(name);
                Py_XDECREF(py_args);
                Py_XDECREF(py_ret);
                Py_XDECREF(has_error);
                Py_XDECREF(is_construct_call);
                Py_XDECREF(py_ctx_type);
            }

            if (PyErr_Occurred()) return;
            if (!ctx_c->has_debugger) return;

            v8::Local<v8::Object> global = ctx->Global();
            v8::MaybeLocal<v8::Value> maybe_faker, maybe_construct;
            v8::Local<v8::Value> faker, construct;
            maybe_faker = global->Get(
                ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Faker));
            if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
                maybe_construct = faker.As<v8::Object>()->Get(
                    ctx, runtime_manager.find_isolate(isolate)->v8_string(V8StringId::Construct));
                if (maybe_construct.ToLocal(&construct) && construct->IsFunction()) {

                    if (args.IsEmpty()) {
                        args = GetArgsArray();
                    }
                    if (ret.IsEmpty()) {
                        ret = GetResultValue();
                    }

                    v8::Local<v8::Value> params[] = {
                        V8String(isolate, func_name), args, ret,
                        v8::Boolean::New(isolate, has_caught),
                        v8::Boolean::New(isolate, info.IsConstructCall())
                    };
                    v8::MaybeLocal<v8::Value> return_value = construct.As<v8::Function>()->Call(
                            ctx, faker,
                            5, params);
                    if (return_value.IsEmpty()) {
                    }
                }
            }
        }
    }


protected:
    const v8::FunctionCallbackInfo<v8::Value>& info;
    const char* func_name;
    FAKER_DEFINE

};

}  // namespace faker

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FAKER_FAKER_HOOK_H_
