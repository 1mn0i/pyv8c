#include "Faker.h"


// Extracts a C string from a V8 Utf8Value.
const char* ToCString(const String::Utf8Value& value) {
    return *value ? *value : "<string conversion failed>";
}

// Reads a file into a v8 string.
MaybeLocal<String> ReadFile(Isolate* isolate, const char* name) {
    FILE* file = fopen(name, "rb");
    if (file == NULL) return MaybeLocal<String>();

    fseek(file, 0, SEEK_END);
    size_t size = ftell(file);
    rewind(file);

    char* chars = new char[size + 1];
    chars[size] = '\0';
    for (size_t i = 0; i < size;) {
        i += fread(&chars[i], 1, size - i, file);
        if (ferror(file)) {
            fclose(file);
            return MaybeLocal<String>();
        }
    }
    fclose(file);
    MaybeLocal<String> result = String::NewFromUtf8(isolate, chars, NewStringType::kNormal, static_cast<int>(size));
    delete[] chars;
    return result;
}

void ReportException(Isolate* isolate, TryCatch* try_catch) {
    HandleScope handle_scope(isolate);
    String::Utf8Value exception(isolate, try_catch->Exception());
    const char* exception_string = ToCString(exception);
    Local<Message> message = try_catch->Message();
    if (message.IsEmpty()) {
        // V8 didn't provide any extra information about this error; just
        // print the exception.
        fprintf(stderr, "%s\n", exception_string);
    }
    else {
        // Print (filename):(line number): (message).
        String::Utf8Value filename(isolate,
            message->GetScriptOrigin().ResourceName());
        Local<Context> context(isolate->GetCurrentContext());
        const char* filename_string = ToCString(filename);
        int linenum = message->GetLineNumber(context).FromJust();
        fprintf(stderr, "%s:%i: %s\n", filename_string, linenum, exception_string);
        // Print line of source code.
        String::Utf8Value sourceline(
            isolate, message->GetSourceLine(context).ToLocalChecked());
        const char* sourceline_string = ToCString(sourceline);
        fprintf(stderr, "%s\n", sourceline_string);
        // Print wavy underline (GetUnderline is deprecated).
        int start = message->GetStartColumn(context).FromJust();
        for (int i = 0; i < start; i++) {
            fprintf(stderr, " ");
        }
        int end = message->GetEndColumn(context).FromJust();
        for (int i = start; i < end; i++) {
            fprintf(stderr, "^");
        }
        fprintf(stderr, "\n");
        Local<Value> stack_trace_string;
        if (try_catch->StackTrace(context).ToLocal(&stack_trace_string) &&
            stack_trace_string->IsString() &&
            stack_trace_string.As<String>()->Length() > 0) {
            String::Utf8Value stack_trace(isolate, stack_trace_string);
            const char* err = ToCString(stack_trace);
            fprintf(stderr, "%s\n", err);
        }
    }
}

// Executes a string within the current v8 context.
bool ExecuteString(Isolate* isolate, Local<String> source,
    Local<Value> name, bool print_result,
    bool report_exceptions) {
    HandleScope handle_scope(isolate);
    TryCatch try_catch(isolate);
    ScriptOrigin origin(name);
    Local<Context> context(isolate->GetCurrentContext());
    Local<Script> script;
    if (!Script::Compile(context, source, &origin).ToLocal(&script)) {
        // Print errors that happened during compilation.
        if (report_exceptions)
            ReportException(isolate, &try_catch);
        return false;
    }
    else {
        Local<Value> result;
        if (!script->Run(context).ToLocal(&result)) {
            assert(try_catch.HasCaught());
            // Print errors that happened during execution.
            if (report_exceptions)
                ReportException(isolate, &try_catch);
            return false;
        }
        else {
            assert(!try_catch.HasCaught());
            if (print_result && !result->IsUndefined()) {
                // If all went well and the result wasn't undefined then print
                // the returned value.
                String::Utf8Value str(isolate, result);
                const char* cstr = ToCString(str);
                printf("%s\n", cstr);
            }
            return true;
        }
    }
}

void DocumentAllCallback(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> context = isolate->GetCurrentContext();

    if (info.Data().IsEmpty()) {
        info.GetReturnValue().SetNull();
    }
    else {
        Local<Value> param[10];
        for (int i = 0; i < info.Length(); i++) {
            param[i] = info[i];
        }
        Local<Function> call_all = info.Data().As<Function>();
        Local<Value> result = call_all->Call(context, Null(isolate), info.Length(), param).ToLocalChecked();
        info.GetReturnValue().Set(result);
    }

}

void NativeWrapperCallback(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> context = isolate->GetCurrentContext();

    Local<Value> param[10];
    for (int i = 0; i < info.Length(); i++) {
        param[i] = info[i];
    }

    Local<Object> handler = info.Data().As<Object>();
    Local<Function> func = handler->Get(context, String::NewFromUtf8(isolate, "cb").ToLocalChecked()).ToLocalChecked().As<Function>();

    TryCatch tryCatch(isolate);
    MaybeLocal<Value> maybe_result = func->Call(context, info.This(), info.Length(), param);
    Local<Value> result;
    if (tryCatch.HasCaught()) {
        result = tryCatch.Exception();
        tryCatch.Reset();
        isolate->ThrowException(result);
        if (tryCatch.HasCaught()) {
            tryCatch.ReThrow();
        }
    }
    else {
        result = maybe_result.FromMaybe(Undefined(isolate).As<Value>());
        info.GetReturnValue().Set(result);
    }
}


void Faker::Print(const FunctionCallbackInfo<Value>& info) {
    for (int i = 0; i < info.Length(); i++) {
        HandleScope handle_scope(info.GetIsolate());
        String::Utf8Value str(info.GetIsolate(), info[i]);
        printf("%s ", *str);
    }
    printf("\n");
    fflush(stdout);
}

void Faker::Read(const FunctionCallbackInfo<Value>& info) {
    if (info.Length() != 1) {
        info.GetIsolate()->ThrowError("Bad parameters");
        return;
    }
    String::Utf8Value file(info.GetIsolate(), info[0]);
    if (*file == NULL) {
        info.GetIsolate()->ThrowError("Error loading file");
        return;
    }
    Local<String> source;
    if (!ReadFile(info.GetIsolate(), *file).ToLocal(&source)) {
        info.GetIsolate()->ThrowError("Error loading file");
        return;
    }

    info.GetReturnValue().Set(source);
}

void Faker::RunJS(const FunctionCallbackInfo<Value>& info) {
    if (info.Length() != 2) {
        info.GetIsolate()->ThrowError("Bad parameters");
        return;
    }
    HandleScope handle_scope(info.GetIsolate());
    Local<String> source = info[0].As<String>();
    if (!ExecuteString(info.GetIsolate(), source, info[1], false, true)) {
        info.GetIsolate()->ThrowError("Error executing js");
        return;
    }
}

void Faker::RunJSFile(const FunctionCallbackInfo<Value>& info) {
    for (int i = 0; i < info.Length(); i++) {
        HandleScope handle_scope(info.GetIsolate());
        String::Utf8Value file(info.GetIsolate(), info[i]);
        if (*file == NULL) {
            info.GetIsolate()->ThrowError("Error loading file");
            return;
        }
        Local<String> source;
        if (!ReadFile(info.GetIsolate(), *file).ToLocal(&source)) {
            info.GetIsolate()->ThrowError("Error loading file");
            return;
        }
        if (!ExecuteString(info.GetIsolate(), source, info[i], false, true)) {
            info.GetIsolate()->ThrowError("Error executing file");
            return;
        }
    }
}

void Faker::DocumentAll(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> context = isolate->GetCurrentContext();

    Local<FunctionTemplate> t = FunctionTemplate::New(isolate);
    t->InstanceTemplate()->MarkAsUndetectable();

    if (info.Length() > 0 && info[0]->IsFunction()) {
        Local<Function> call_all = info[0].As<Function>();
        t->InstanceTemplate()->SetCallAsFunctionHandler(DocumentAllCallback, call_all);
    }
    else {
        t->InstanceTemplate()->SetCallAsFunctionHandler(DocumentAllCallback);
    }

    Local<Object> obj = t->GetFunction(context).ToLocalChecked()->NewInstance(context).ToLocalChecked();
    info.GetReturnValue().Set(obj);
}
    
void Faker::NativeWrapper(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> context = isolate->GetCurrentContext();

    if (info.Length() > 0 && info[0]->IsObject()) {
        Local<Object> handler = info[0].As<Object>();
        if (handler->HasOwnProperty(context, String::NewFromUtf8(isolate, "cb").ToLocalChecked()).ToChecked()) {

            Local<FunctionTemplate> func_temp = FunctionTemplate::New(isolate, NativeWrapperCallback, handler);

            if (handler->HasOwnProperty(context, String::NewFromUtf8(isolate, "name").ToLocalChecked()).ToChecked()) {
                Local<String> name = handler->Get(context, String::NewFromUtf8(isolate, "name").ToLocalChecked()).ToLocalChecked().As<String>();
                func_temp->SetClassName(name);
            }

            if (handler->HasOwnProperty(context, String::NewFromUtf8(isolate, "length").ToLocalChecked()).ToChecked()) {
                Local<Number> length = handler->Get(context, String::NewFromUtf8(isolate, "length").ToLocalChecked()).ToLocalChecked().As<Number>();
                func_temp->SetLength(length->Value());
            }

            Local<Function>func = func_temp->GetFunction(context).ToLocalChecked();
            info.GetReturnValue().Set(func);
        }
    }
}

void Faker::GetArg(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> ctx = isolate->GetCurrentContext();

    if (info.Length() == 2 && info[0]->IsObject() && info[1]->IsString()) {
        Local<Object> targert = info[0].As<Object>();
        Local<Value> val;
        if (targert->GetPrivate(ctx, v8::Private::ForApi(isolate, info[1].As<String>())).ToLocal(&val)) {
            info.GetReturnValue().Set(val);
            return;
        }
    }
    info.GetIsolate()->ThrowException(Exception::TypeError(V8String(isolate, "get arg failed")));
}

void Faker::SetArg(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> ctx = isolate->GetCurrentContext();

    if (info.Length() == 3 && info[0]->IsObject() && info[1]->IsString()) {
        Local<Object> targert = info[0].As<Object>();
        if (targert->SetPrivate(ctx, v8::Private::ForApi(isolate, info[1].As<String>()), info[2]).IsJust()) {
            info.GetReturnValue().Set(v8::True(isolate));
            return;
        }
    }
    info.GetIsolate()->ThrowException(Exception::TypeError(V8String(isolate, "set arg failed")));
}

void Faker::CreateConstructor(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> ctx = isolate->GetCurrentContext();
        
    if (info.Length() == 1 && info[0]->IsString()) {
        v8::Local<v8::FunctionTemplate> interface_template = FunctionTemplate::New(isolate, faker::IllegalConstructorCallback);
        interface_template->SetClassName(info[0].As<String>());
        interface_template->PrototypeTemplate()->Set(Symbol::GetToStringTag(isolate), info[0].As<String>(), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
        info.GetReturnValue().Set(interface_template->GetFunction(ctx).ToLocalChecked());
        return;
    }
    if (info.Length() == 3 && info[0]->IsString() && info[1]->IsInt32() && info[2]->IsFunction()) {
        v8::Local<v8::FunctionTemplate> interface_template = FunctionTemplate::New(isolate, faker::JsFunctionCallback, info[2].As<Function>());
        interface_template->SetClassName(info[0].As<String>());
        interface_template->SetLength(info[1].As<Integer>()->Value());
        interface_template->PrototypeTemplate()->Set(Symbol::GetToStringTag(isolate), info[0].As<String>(), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
        info.GetReturnValue().Set(interface_template->GetFunction(ctx).ToLocalChecked());
        return;
    }
}


void Faker::GetHook(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> ctx = isolate->GetCurrentContext();
    v8::Local<v8::Object> global = ctx->Global();
    v8::Local<v8::Private> key = v8::Private::ForApi(isolate, V8String(isolate, "hook"));
    v8::MaybeLocal<v8::Value> maybe_value = global->GetPrivate(ctx, key);
    bool hook = false;
    if (!maybe_value.IsEmpty()) {
        hook = maybe_value.ToLocalChecked()->IsTrue();
    }
    info.GetReturnValue().Set(Boolean::New(isolate, hook));
}

void Faker::SetHook(const FunctionCallbackInfo<Value>& info) {
    Isolate* isolate = info.GetIsolate();
    Local<Context> ctx = isolate->GetCurrentContext();
    v8::Local<v8::Object> global = ctx->Global();
    v8::Local<v8::Private> key = v8::Private::ForApi(isolate, V8String(isolate, "hook"));
    if (info.Length() == 1 && info[0]->IsBoolean()) {
        global->SetPrivate(ctx, key, info[0]);
    }
}

void Faker::Register(Isolate* isolate, Local<FunctionTemplate>& global_inteface_template) {
    v8::Local<v8::FunctionTemplate> interface_template_Faker = FunctionTemplate::New(isolate);
    Local<ObjectTemplate> prototype_template_Faker = interface_template_Faker->PrototypeTemplate();
    interface_template_Faker->SetLength(0);
    interface_template_Faker->SetClassName(V8String(isolate, HOME_NAME));

    prototype_template_Faker->Set(Symbol::GetToStringTag(isolate), V8String(isolate, HOME_NAME), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    prototype_template_Faker->Set(isolate, "new_document_all", FunctionTemplate::New(isolate, DocumentAll));
    prototype_template_Faker->Set(isolate, "native_wrapper", FunctionTemplate::New(isolate, NativeWrapper));
    prototype_template_Faker->Set(isolate, "run_js", FunctionTemplate::New(isolate, RunJS)); 
    prototype_template_Faker->Set(isolate, "run_js_file", FunctionTemplate::New(isolate, RunJSFile));
    prototype_template_Faker->Set(isolate, "v8_version", V8String(isolate, v8::V8::GetVersion()));
    prototype_template_Faker->Set(isolate, "print", FunctionTemplate::New(isolate, Print));
    //prototype_template_Faker->Set(isolate, "new_symbol", Symbol::New(isolate, V8String(isolate, "new_symbol")));

    prototype_template_Faker->Set(isolate, "get_arg", FunctionTemplate::New(isolate, GetArg));
    prototype_template_Faker->Set(isolate, "set_arg", FunctionTemplate::New(isolate, SetArg));

    prototype_template_Faker->SetAccessorProperty(V8String(isolate, "hook"), FunctionTemplate::New(isolate, GetHook), FunctionTemplate::New(isolate, SetHook), PropertyAttribute(v8::DontEnum | v8::DontDelete));

    //prototype_template_Faker->Set(isolate, "create_constructor", FunctionTemplate::New(isolate, CreateConstructor));

    //Local<Object> faker_obj = interface_template_Faker->InstanceTemplate()->NewInstance(ctx).ToLocalChecked();
    //ctx->Global()->DefineOwnProperty(ctx, V8String(isolate, HOME_NAME), faker_obj, PropertyAttribute(v8::DontEnum | v8::DontDelete));
    global_inteface_template->InstanceTemplate()->Set(V8String(isolate, HOME_NAME), interface_template_Faker->InstanceTemplate(), PropertyAttribute(v8::DontEnum | v8::DontDelete));

}

