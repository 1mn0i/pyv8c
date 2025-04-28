#include "Window.h"

namespace faker {

    void WindowAttributeGetCallback(const v8::FunctionCallbackInfo<v8::Value>& info) {
        auto isolate = info.GetIsolate();
        auto context = isolate->GetCurrentContext();
        info.GetReturnValue().Set(context->Global());
    }


    Local<ObjectTemplate> WindowInterface(Isolate* isolate) {
        // EventTarget
        Local<FunctionTemplate> interface_template_EventTarget = FunctionTemplate::New(isolate);
        interface_template_EventTarget->SetCallHandler(EventTargetConstructor);
        interface_template_EventTarget->SetLength(0);
        interface_template_EventTarget->SetClassName(V8String(isolate, "EventTarget"));
        Local<ObjectTemplate> prototype_template_EventTarget = interface_template_EventTarget->PrototypeTemplate();
        prototype_template_EventTarget->Set(Symbol::GetToStringTag(isolate), V8String(isolate, "EventTarget"), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
        Local<Signature> signature_EventTarget = Signature::New(isolate, interface_template_EventTarget);
        OperationItem operationTable[] = {
            {"addEventListener", 2, AddEventListenerCallback, static_cast<PropertyAttribute>(PropertyAttribute::None)},
            {"dispatchEvent", 1, DispatchEventCallback, static_cast<PropertyAttribute>(PropertyAttribute::None)},
            {"removeEventListener", 2, RemoveEventListenerCallback, static_cast<PropertyAttribute>(PropertyAttribute::None)},
        };
        InstallOperations(isolate, prototype_template_EventTarget, CONFIGS(operationTable), signature_EventTarget);
        interface_template_EventTarget->ReadOnlyPrototype();
        interface_template_EventTarget->PrototypeTemplate()->SetImmutableProto();
        interface_template_EventTarget->InstanceTemplate()->SetImmutableProto();


        // WindowProperties
        Local<FunctionTemplate> interface_template_WindowProperties = FunctionTemplate::New(isolate);
        interface_template_WindowProperties->SetLength(0);
        interface_template_WindowProperties->SetClassName(V8String(isolate, "WindowProperties"));
        interface_template_WindowProperties->Inherit(interface_template_EventTarget);
        Local<ObjectTemplate> prototype_template_WindowProperties = interface_template_WindowProperties->PrototypeTemplate();
        prototype_template_WindowProperties->Set(Symbol::GetToStringTag(isolate), V8String(isolate, "WindowProperties"), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
        interface_template_WindowProperties->ReadOnlyPrototype();
        interface_template_WindowProperties->PrototypeTemplate()->SetImmutableProto();
        interface_template_WindowProperties->InstanceTemplate()->SetImmutableProto();

        // Window
        v8::Local<v8::FunctionTemplate> interface_template_Window = FunctionTemplate::New(isolate, IllegalConstructorCallback);
        Local<ObjectTemplate> prototype_template_Window = interface_template_Window->PrototypeTemplate();
        interface_template_Window->SetLength(0);
        interface_template_Window->SetClassName(V8String(isolate, "Window"));
        interface_template_Window->Inherit(interface_template_WindowProperties);
        prototype_template_Window->Set(Symbol::GetToStringTag(isolate), V8String(isolate, "Window"), PropertyAttribute(v8::ReadOnly | v8::DontEnum));
        ConstantItem constantTable[] = {
            {"PERSISTENT", 1, static_cast<PropertyAttribute>(PropertyAttribute::ReadOnly | PropertyAttribute::DontDelete)},
            {"TEMPORARY", 0, static_cast<PropertyAttribute>(PropertyAttribute::ReadOnly | PropertyAttribute::DontDelete)},
        };
        InstallConstants(isolate, prototype_template_Window, CONFIGS(constantTable));
        Local<ObjectTemplate> instance_template = interface_template_Window->InstanceTemplate();
        AttributeItem attributeTable[] = {
            {"window", WindowAttributeGetCallback, nullptr, PropertyAttribute::DontDelete},
            {"self", WindowAttributeGetCallback, WINDOW_ATTRIBUTE_SET_CALLBACK("self"), PropertyAttribute::None},
            {"parent", WindowAttributeGetCallback, WINDOW_ATTRIBUTE_SET_CALLBACK("parent"), PropertyAttribute::None},
            {"frames", WindowAttributeGetCallback, WINDOW_ATTRIBUTE_SET_CALLBACK("frames"), PropertyAttribute::None},
        };
        Local<Signature> signature_Window = Signature::New(isolate, interface_template_Window);
        InstallAttributes(isolate, instance_template, CONFIGS(attributeTable), signature_Window);

        instance_template->Set(V8String(isolate, "Window"), interface_template_Window, static_cast<PropertyAttribute>(PropertyAttribute::DontEnum));
        instance_template->Set(V8String(isolate, "EventTarget"), interface_template_EventTarget, static_cast<PropertyAttribute>(PropertyAttribute::DontEnum));

        interface_template_Window->ReadOnlyPrototype();
        interface_template_Window->PrototypeTemplate()->SetImmutableProto();
        interface_template_Window->InstanceTemplate()->SetImmutableProto();
        return instance_template;
    }

} // namespace faker
