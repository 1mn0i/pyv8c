#include "vm.h"

using namespace v8;

namespace faker {


    void Register(Isolate* isolate, Local<FunctionTemplate>& global_inteface_template) {

        //Local<ObjectTemplate> glob_instance_template = WindowInterface(isolate);
        //v8::Local<v8::Context> ctx = v8::Context::New(isolate, nullptr, glob_instance_template);
        //context.Reset(isolate, ctx);
        //v8::Context::Scope hs(ctx);
        Faker::Register(isolate, global_inteface_template);

        // delete WindowProperties.constructor
        //ctx->Global()
        //    ->GetPrototype().As<Object>()
        //    ->GetPrototype().As<Object>()
        //    ->GetPrototype().As<Object>()
        //    ->Delete(ctx, V8String(isolate, "constructor")).ToChecked();
    }

} // namespace faker
