// Pair iteration, as Chromium's PairSyncIterable gives it to an interface: the
// binding installs the views, the host only answers "the pair at index N".
#include "bridge_base.h"
#include <v8.h>
#include <Python.h>

#include <string>

#include "crossing.h"
#include "callback_boundary.h"
#include "convert.h"
#include "pyclass.h"
#include "idl_spec.h"
#include "pyclass_handlers.h"
#include "iterable.h"

using namespace v8;

enum PairIteratorKind {
    kPairKeys = 0,
    kPairValues = 1,
    kPairEntries = 2,
};

// A private weak host object retains the cursor without entering the function cache.
enum { kPairReceiverField = 0, kPairKindField = 1, kPairSourceField = 2, kPairFieldCount = 3 };

static bool pair_iterable(const py_class* self)
{
    return self->idl_spec != nullptr && ((IdlSpec*)self->idl_spec)->iteration_kind >= kIterationPair;
}

static void pair_illegal_invocation(Isolate* isolate)
{
    isolate->ThrowException(Exception::TypeError(JSTR("Illegal invocation")));
}

static void pair_result(Isolate* isolate, Local<Context> context,
                        const FunctionCallbackInfo<Value>& info,
                        bool done, Local<Value> value)
{
    Local<Object> result = Object::New(isolate);
    if (!result->CreateDataProperty(context, JSTR("done"),
                                    done ? True(isolate).As<Value>() : False(isolate).As<Value>()).FromMaybe(false) ||
        !result->CreateDataProperty(context, JSTR("value"), value).FromMaybe(false))
        return;
    info.GetReturnValue().Set(result);
}

static Local<Object> create_iteration_source(py_class* self, Local<Object> receiver, Local<Context> context)
{
    auto* isolate = v8_compat::context_isolate(context);
    if (receiver->InternalFieldCount() < 2 ||
        receiver->GetInternalField(0) != self->isolate_state->v8_string(V8StringId::PythonObjectMagic)) {
        pair_illegal_invocation(isolate); return {};
    }
    PyObject* instance = IsolateState::python_object(receiver);
    if (instance == nullptr) { js_throw_py(); return {}; }
    const int matches = PyObject_IsInstance(instance, self->cls);
    if (matches < 0) { js_throw_py(); return {}; }
    if (!matches) { pair_illegal_invocation(isolate); return {}; }
    PyObject* callback = ((IdlSpec*)self->idl_spec)->iteration_source;
    Py_XINCREF(callback);
    PyOwnedRef factory(callback);
    if (factory.get() == nullptr) { js_throw_py(); return {}; }
    PyObject* args[] = {instance};
    PyOwnedRef source(call_handler(factory.get(), args, 1));
    if (source.get() == nullptr) { js_throw_py(); return {}; }
    if (!context_check_callback(context)) return {};
    if (!PyCallable_Check(source.get())) {
        isolate->ThrowException(Exception::TypeError(JSTR("iteration source factory must return a callable")));
        return {};
    }
    auto templ = ObjectTemplate::New(isolate);
    templ->SetInternalFieldCount(2);
    Local<Object> holder;
    if (!templ->NewInstance(context).ToLocal(&holder)) return {};
    if (!self->isolate_state->track_python_object(holder, source.get(), context_from_v8(context))) {
        js_throw_py(); return {};
    }
    return holder;
}

static void pair_iterator_next(const FunctionCallbackInfo<Value>& info)
{
    SETUP;
    if (!context_check_callback(context)) return;
    auto* self = (py_class*)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    Local<Object> iterator = info.This();
    Local<Context> owner_context;
    if (!iterator->GetCreationContext().ToLocal(&owner_context) ||
        !context_check_callback(owner_context)) return;
    PyObject* cached = context_registry_get(context_from_v8(owner_context), self->cls_name, REGISTRY_TEMPLATE);
    if (cached == nullptr || !PyObject_TypeCheck(cached, py_class_type)) {
        pair_illegal_invocation(isolate); return;
    }
    auto* owner = (py_class*)cached;
    if (owner->cls != self->cls || owner->iterator_templ.IsEmpty() ||
        !owner->iterator_templ.Get(isolate)->HasInstance(iterator) ||
        iterator->InternalFieldCount() != kPairFieldCount) {
        pair_illegal_invocation(isolate); return;
    }
    Local<Value> source = iterator->GetInternalField(kPairSourceField).As<Value>();
    if (!source->IsObject()) { pair_illegal_invocation(isolate); return; }
    PyObject* callable = IsolateState::python_object(source.As<Object>());
    if (callable == nullptr) { js_throw_py(); return; }
    PyOwnedRef keep_callable(PyV8_NewRef(callable));
    PyOwnedRef item(PyObject_Vectorcall(keep_callable.get(), nullptr, 0, nullptr));
    if (item.get() == nullptr) { js_throw_py(); return; }
    if (!context_check_callback(context)) return;
    if (item.get() == Py_None) {
        pair_result(isolate, context, info, true, Undefined(isolate));
        return;
    }
    PyOwnedRef sequence(PySequence_Fast(item.get(), "iteration source must return a pair or None"));
    if (sequence.get() == nullptr) { js_throw_py(); return; }
    if (PySequence_Size(sequence.get()) != 2) {
        isolate->ThrowException(Exception::TypeError(JSTR("iteration source must return a pair or None")));
        return;
    }
    PyOwnedRef py_key(PySequence_GetItem(sequence.get(), 0));
    PyOwnedRef py_value(PySequence_GetItem(sequence.get(), 1));
    if (py_key.get() == nullptr || py_value.get() == nullptr) { js_throw_py(); return; }
    Local<Value> key = js_from_py(py_key.get(), context), value = js_from_py(py_value.get(), context);
    if (key.IsEmpty() || value.IsEmpty() || PyErr_Occurred()) { js_throw_py(); return; }
    Local<Array> entry = Array::New(isolate, 2);
    if (!entry->CreateDataProperty(context, 0, key).FromMaybe(false) ||
        !entry->CreateDataProperty(context, 1, value).FromMaybe(false)) return;
    const int kind = iterator->GetInternalField(kPairKindField).As<Int32>()->Value();
    pair_result(isolate, context, info, false, kind == kPairKeys ? key : kind == kPairValues ? value : entry.As<Value>());
}

static bool select_iteration_realm(py_class*& self, Local<Object> receiver, Local<Context>& context)
{
    auto* isolate = v8_compat::context_isolate(context);
    if (receiver->InternalFieldCount() < 2 ||
        receiver->GetInternalField(0) != self->isolate_state->v8_string(V8StringId::PythonObjectMagic)) {
        pair_illegal_invocation(isolate); return false;
    }
    if (!receiver->GetCreationContext().ToLocal(&context) || !context_check_callback(context)) return false;
    auto* owner = context_from_v8(context);
    PyObject* cached = context_registry_get(owner, self->cls_name, REGISTRY_TEMPLATE);
    if (cached == nullptr || !PyObject_TypeCheck(cached, py_class_type)) {
        pair_illegal_invocation(isolate); return false;
    }
    auto* target = (py_class*)cached;
    if (target->cls != self->cls || target->iterator_templ.IsEmpty()) {
        pair_illegal_invocation(isolate); return false;
    }
    self = target;
    return true;
}

static void pair_view(const FunctionCallbackInfo<Value>& info, PairIteratorKind kind)
{
    SETUP;
    if (!context_check_callback(context)) return;
    py_class* self = (py_class*)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    if (!select_iteration_realm(self, info.This(), context)) return;
    Context::Scope receiver_realm(context);
    Local<FunctionTemplate> iterator_template = self->iterator_templ.Get(isolate);
    if (iterator_template.IsEmpty()) { pair_illegal_invocation(isolate); return; }
    Local<Function> constructor;
    Local<Object> iterator;
    if (!without_gil([&] { return iterator_template->GetFunction(context).ToLocal(&constructor); }) ||
        !without_gil([&] { return constructor->NewInstance(context).ToLocal(&iterator); }))
    {
        js_throw_py();
        return;
    }
    auto source = create_iteration_source(self, info.This(), context);
    if (source.IsEmpty()) return;
    iterator->SetInternalField(kPairSourceField, source);
    iterator->SetInternalField(kPairReceiverField, info.This());
    iterator->SetInternalField(kPairKindField, Int32::New(isolate, (int)kind));
    info.GetReturnValue().Set(iterator);
}

static void pair_iterator_keys(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairKeys); }
static void pair_iterator_values(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairValues); }
static void pair_iterator_entries(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairEntries); }

static void pair_for_each(const FunctionCallbackInfo<Value>& info)
{
    SETUP;
    if (!context_check_callback(context)) return;
    if (info.Length() < 1 || !info[0]->IsFunction()) {
        isolate->ThrowException(Exception::TypeError(JSTR("forEach callback must be callable")));
        return;
    }
    py_class* self = (py_class*)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
    if (!select_iteration_realm(self, info.This(), context)) return;
    Context::Scope receiver_realm(context);
    Local<Function> constructor;
    Local<Object> iterator;
    if (!self->iterator_templ.Get(isolate)->GetFunction(context).ToLocal(&constructor) ||
        !constructor->NewInstance(context).ToLocal(&iterator)) return;
    auto source = create_iteration_source(self, info.This(), context);
    if (source.IsEmpty()) return;
    iterator->SetInternalField(kPairSourceField, source);
    iterator->SetInternalField(kPairReceiverField, info.This());
    iterator->SetInternalField(kPairKindField, Int32::New(isolate, kPairEntries));
    Local<Function> next;
    auto next_template = FunctionTemplate::New(isolate, CallbackBoundary<pair_iterator_next>::call,
        info.Data(), Signature::New(isolate, self->iterator_templ.Get(isolate)), 0, ConstructorBehavior::kThrow);
    if (!next_template->GetFunction(context).ToLocal(&next)) return;
    Local<Value> this_arg = info.Length() > 1 ? info[1] : Undefined(isolate).As<Value>();
    for (;;) {
        Local<Value> result;
        if (!without_gil([&] { return next->Call(context, iterator, 0, nullptr).ToLocal(&result); })) return;
        Local<Value> done, entry;
        if (!result.As<Object>()->Get(context, V8String(isolate, "done")).ToLocal(&done)) return;
        if (done->BooleanValue(isolate)) return;
        if (!result.As<Object>()->Get(context, V8String(isolate, "value")).ToLocal(&entry)) return;
        Local<Value> args[3];
        if (!entry.As<Object>()->Get(context, 1).ToLocal(&args[0]) ||
            !entry.As<Object>()->Get(context, 0).ToLocal(&args[1])) return;
        args[2] = info.This();
        Local<Value> ignored;
        if (!without_gil([&] { return info[0].As<Function>()->Call(context, this_arg, 3, args).ToLocal(&ignored); })) return;
    }
}

static Local<Object> prototype_object_of(Isolate* isolate, Local<Context> context,
                                         Local<FunctionTemplate> templ)
{
    Local<Function> function;
    if (!without_gil([&] { return templ->GetFunction(context).ToLocal(&function); }))
        return Local<Object>();
    Local<Value> value;
    if (!function->Get(context, V8String(isolate, "prototype")).ToLocal(&value) || !value->IsObject())
        return Local<Object>();
    return value.As<Object>();
}

bool install_sync_iterable(Isolate* isolate, py_class* self, Local<External> js_self,
                           Local<FunctionTemplate> interface_template, Local<Signature> signature)
{
    if (self->idl_spec == nullptr) return true;
    const auto& config = *(IdlSpec*)self->idl_spec;
    if (config.iteration_kind == kIterationIndexed) {
        const int64_t flag = config.array_methods;
        auto prototype = interface_template->PrototypeTemplate();
        if (flag & 1) prototype->SetIntrinsicDataProperty(Symbol::GetIterator(isolate), kArrayProto_values, DontEnum);
        if (flag & 2) prototype->SetIntrinsicDataProperty(V8String(isolate, "entries"), kArrayProto_entries, v8::None);
        if (flag & 4) prototype->SetIntrinsicDataProperty(V8String(isolate, "keys"), kArrayProto_keys, v8::None);
        if (flag & 8) prototype->SetIntrinsicDataProperty(V8String(isolate, "values"), kArrayProto_values, v8::None);
        if (flag & 16) prototype->SetIntrinsicDataProperty(V8String(isolate, "forEach"), kArrayProto_forEach, v8::None);
        return true;
    }
    (void)signature;
    if (!pair_iterable(self))
        return true;
    const char* class_name = PyUnicode_AsUTF8_abi3(self->cls_name);
    if (class_name == nullptr)
        return false;
    std::string iterator_name(class_name);
    iterator_name += " Iterator";
    Local<String> tag = V8String(isolate, iterator_name.c_str());

    // The iterator's prototype inherits V8's own %IteratorPrototype%, as Chromium
    // builds it.
    Local<FunctionTemplate> iterator = FunctionTemplate::New(
        isolate, nullptr, Local<Value>(), Local<Signature>(), 0, ConstructorBehavior::kAllow);
    Local<FunctionTemplate> intrinsic = FunctionTemplate::New(
        isolate, nullptr, Local<Value>(), Local<Signature>(), 0, ConstructorBehavior::kThrow);
    intrinsic->RemovePrototype();
    intrinsic->SetIntrinsicDataProperty(V8String(isolate, "prototype"), Intrinsic::kIteratorPrototype);
    iterator->Inherit(intrinsic);
    iterator->ReadOnlyPrototype();
    iterator->SetClassName(tag);
    iterator->PrototypeTemplate()->Set(Symbol::GetToStringTag(isolate), tag,
                                       static_cast<PropertyAttribute>(ReadOnly | DontEnum));
    Local<FunctionTemplate> next = FunctionTemplate::New(
        isolate, CallbackBoundary<pair_iterator_next>::call, js_self,
        Local<Signature>(), 0, ConstructorBehavior::kThrow);
    next->SetClassName(V8String(isolate, "next"));
    iterator->PrototypeTemplate()->Set(V8String(isolate, "next"), next, v8::None);
    iterator->InstanceTemplate()->SetInternalFieldCount(kPairFieldCount);
    self->iterator_templ.Set(self->isolate_state, isolate, iterator);
    return true;
}

void install_sync_iterator_prototype(py_class* self, Local<Context> context)
{
    if (!pair_iterable(self) || self->iterator_templ.IsEmpty())
        return;
    Isolate* isolate = v8_compat::context_isolate(context);
    Local<Object> iterator_prototype =
        prototype_object_of(isolate, context, self->iterator_templ.Get(isolate));
    if (!iterator_prototype.IsEmpty())
        properties_delete_constructor(iterator_prototype, context);
    Local<Object> prototype = prototype_object_of(isolate, context, self->templ.Get(isolate));
    if (prototype.IsEmpty())
        return;

    // On the prototype object, not its template: a member left to a placeholder
    // has one there by now, and these are what the page must reach instead.
    Local<External> anchor = self->isolate_state->callback_data(
        context_from_v8(context), (PyObject*)self);
    if (anchor.IsEmpty())
        return;
    const struct { const char* name; FunctionCallback callback; } views[] = {
        {"keys", CallbackBoundary<pair_iterator_keys>::call},
        {"values", CallbackBoundary<pair_iterator_values>::call},
        {"entries", CallbackBoundary<pair_iterator_entries>::call},
    };
    Local<Function> default_method;
    const auto& config = *(IdlSpec*)self->idl_spec;
    for (const auto& view : views)
    {
        const int bit = std::string(view.name) == "keys" ? 4 : (std::string(view.name) == "values" ? 8 : 2);
        const bool is_default = std::string(view.name) == (config.iteration_kind == kIterationSet ? "values" : "entries");
        if (!(config.iteration_methods & bit) && !(is_default && (config.iteration_methods & 1))) continue;
        Local<FunctionTemplate> templ = FunctionTemplate::New(
            isolate, view.callback, anchor, Local<Signature>(), 0, ConstructorBehavior::kThrow);
        templ->SetClassName(V8String(isolate, view.name));
        Local<Function> function;
        if (!without_gil([&] { return templ->GetFunction(context).ToLocal(&function); }))
        {
            js_throw_py();
            return;
        }
        if ((config.iteration_methods & bit) && !prototype->DefineOwnProperty(context, V8String(isolate, view.name), function,
                                          static_cast<PropertyAttribute>(None)).FromMaybe(false))
            return;
        if (is_default) default_method = function;
    }
    if (config.iteration_methods & 16) {
        auto templ = FunctionTemplate::New(isolate, CallbackBoundary<pair_for_each>::call, anchor,
                                          Local<Signature>(), 1, ConstructorBehavior::kThrow);
        templ->SetClassName(V8String(isolate, "forEach"));
        Local<Function> function;
        if (!templ->GetFunction(context).ToLocal(&function)) return;
        if (!prototype->DefineOwnProperty(context, V8String(isolate, "forEach"), function, v8::None).FromMaybe(false)) return;
    }
    // The default method is entries for pairs and values for sets.
    if ((config.iteration_methods & 1) && !default_method.IsEmpty())
        prototype->DefineOwnProperty(context, Symbol::GetIterator(isolate), default_method,
                                     static_cast<PropertyAttribute>(DontEnum)).ToChecked();
}
