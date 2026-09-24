// Pair iteration, as Chromium's PairSyncIterable gives it to an interface: the
// binding installs the views, the host only answers "the pair at index N".
#include <v8.h>
#include <Python.h>

#include <string>

#include "pyv8.h"
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

// One iterator's state, in internal fields, where the page cannot see it.
enum {
    kPairReceiverField = 0,
    kPairKindField = 1,
    kPairIndexField = 2,
    kPairFieldCount = 3,
};

static bool pair_iterable(const py_class* self)
{
    return self->idl_spec != nullptr && ((IdlSpec*)self->idl_spec)->iterate != nullptr;
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

static void pair_iterator_next(const FunctionCallbackInfo<Value>& info)
{
    SETUP;
    if (!context_check_callback(context)) return;
    py_class* self = (py_class*)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }

    Local<Object> iterator = info.This();
    Local<Value> receiver = iterator->GetInternalField(kPairReceiverField).As<Value>();
    if (!receiver->IsObject()) { pair_illegal_invocation(isolate); return; }
    PyObject* instance = IsolateState::python_object(receiver.As<Object>());
    if (instance == nullptr) { pair_illegal_invocation(isolate); return; }

    const int kind = iterator->GetInternalField(kPairKindField).As<Int32>()->Value();
    const int index = iterator->GetInternalField(kPairIndexField).As<Int32>()->Value();
    PyObject* handler = py_class_handler(instance, HandlerId::Iterate, context_from_v8(context));
    if (handler == nullptr)
    {
        // No source to read, or a Python error while looking it up.
        if (PyErr_Occurred()) js_throw_py();
        else pair_result(isolate, context, info, true, Undefined(isolate));
        return;
    }
    PyOwnedRef callable(handler);
    PyOwnedRef position(PyLong_FromLong(index));
    if (position.get() == nullptr) { js_throw_py(); return; }
    PyObject* argv[] = {instance, position.get()};
    PyObject* item = call_handler(callable.get(), argv, 2);
    if (item == nullptr) { js_throw_py(); return; }
    PyOwnedRef keep(item);
    if (item == Py_None)
    {
        pair_result(isolate, context, info, true, Undefined(isolate));
        return;
    }

    PyObject* pair = PySequence_Fast(item, "the iteration source must yield a pair or None");
    if (pair == nullptr) { js_throw_py(); return; }
    PyObject* key_object = PySequence_Size(pair) == 2 ? PySequence_GetItem(pair, 0) : nullptr;
    PyObject* value_object = key_object == nullptr ? nullptr : PySequence_GetItem(pair, 1);
    Py_DECREF(pair);
    if (key_object == nullptr || value_object == nullptr)
    {
        Py_XDECREF(key_object);
        Py_XDECREF(value_object);
        if (PyErr_Occurred()) js_throw_py();
        else isolate->ThrowException(Exception::TypeError(
            JSTR("the iteration source must yield a pair or None")));
        return;
    }
    Local<Value> key = js_from_py(key_object, context);
    Local<Value> value = js_from_py(value_object, context);
    Py_DECREF(key_object);
    Py_DECREF(value_object);
    if (key.IsEmpty() || value.IsEmpty() || PyErr_Occurred()) { js_throw_py(); return; }

    Local<Value> produced = key;
    if (kind == kPairValues)
        produced = value;
    else if (kind == kPairEntries)
    {
        Local<Array> entry = Array::New(isolate, 2);
        if (!entry->CreateDataProperty(context, 0, key).FromMaybe(false) ||
            !entry->CreateDataProperty(context, 1, value).FromMaybe(false))
        {
            js_throw_py();
            return;
        }
        produced = entry;
    }
    iterator->SetInternalField(kPairIndexField, Int32::New(isolate, index + 1));
    pair_result(isolate, context, info, false, produced);
}

static void pair_view(const FunctionCallbackInfo<Value>& info, PairIteratorKind kind)
{
    SETUP;
    if (!context_check_callback(context)) return;
    py_class* self = (py_class*)IsolateState::callback_target(info.Data());
    if (self == nullptr) { js_throw_py(); return; }
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
    iterator->SetInternalField(kPairReceiverField, info.This());
    iterator->SetInternalField(kPairKindField, Int32::New(isolate, (int)kind));
    iterator->SetInternalField(kPairIndexField, Int32::New(isolate, 0));
    info.GetReturnValue().Set(iterator);
}

static void pair_iterator_keys(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairKeys); }
static void pair_iterator_values(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairValues); }
static void pair_iterator_entries(const FunctionCallbackInfo<Value>& info) { pair_view(info, kPairEntries); }

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

bool install_pair_iterable(Isolate* isolate, py_class* self, Local<External> js_self,
                           Local<FunctionTemplate> interface_template, Local<Signature> signature)
{
    (void)interface_template;
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
        Signature::New(isolate, iterator), 0, ConstructorBehavior::kThrow);
    next->SetClassName(V8String(isolate, "next"));
    iterator->PrototypeTemplate()->Set(V8String(isolate, "next"), next, v8::None);
    iterator->InstanceTemplate()->SetInternalFieldCount(kPairFieldCount);
    self->iterator_templ.Set(self->isolate_state, isolate, iterator);
    return true;
}

void install_pair_iterator_prototype(py_class* self, Local<Context> context)
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
    Local<Signature> signature = Signature::New(isolate, self->templ.Get(isolate));
    const struct { const char* name; FunctionCallback callback; } views[] = {
        {"keys", CallbackBoundary<pair_iterator_keys>::call},
        {"values", CallbackBoundary<pair_iterator_values>::call},
        {"entries", CallbackBoundary<pair_iterator_entries>::call},
    };
    Local<Function> entries;
    for (const auto& view : views)
    {
        Local<FunctionTemplate> templ = FunctionTemplate::New(
            isolate, view.callback, anchor, signature, 0, ConstructorBehavior::kThrow);
        templ->SetClassName(V8String(isolate, view.name));
        Local<Function> function;
        if (!without_gil([&] { return templ->GetFunction(context).ToLocal(&function); }))
        {
            js_throw_py();
            return;
        }
        if (!prototype->DefineOwnProperty(context, V8String(isolate, view.name), function,
                                          static_cast<PropertyAttribute>(None)).FromMaybe(false))
            return;
        if (std::string(view.name) == "entries")
            entries = function;
    }
    // @@iterator is the entries function itself, as Chromium's bindings define it.
    if (!entries.IsEmpty())
        prototype->DefineOwnProperty(context, Symbol::GetIterator(isolate), entries,
                                     static_cast<PropertyAttribute>(DontEnum)).ToChecked();
}
