#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "runtime.h"
#include "error_family.h"
#include <v8.h>
#include <vector>

#include "convert.h"
#include "jsobject.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "pyclass_handlers.h"
#include "idl_spec.h"
#include "iterable.h"
#include "pyclass_cross_origin_handlers.h"
#include "v8tools.h"

using namespace v8;


enum FlagImmutable {
    instance = 1 << 0,
    prototype = 1 << 1,
};

PyTypeObject* py_class_type = NULL;

static int class_traverse(py_class* self, visitproc visit, void* arg) {
    Py_VISIT(self->cls);
    Py_VISIT(self->idl_spec);
    Py_VISIT(self->cls_name);
    Py_VISIT(self->operation_callback_data);
    Py_VISIT(self->constructor_adapter);
    return 0;
}

static int class_clear(py_class* self) {
    self->templ.Reset();
    self->iterator_templ.Reset();
    Py_CLEAR(self->cls);
    Py_CLEAR(self->idl_spec);
    Py_CLEAR(self->cls_name);
    Py_CLEAR(self->operation_callback_data);
    Py_CLEAR(self->constructor_adapter);
    return 0;
}

static PyType_Slot py_class_slots[] = {
    {Py_tp_traverse, (void*)PythonBoundary<class_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<class_clear>::call},
    {Py_tp_dealloc, (void*)PythonBoundary<py_class_dealloc>::call},
    {Py_tp_doc, (void*)"v8 Class"},
    {0, NULL} };

static PyType_Spec py_class_spec = {
    "pyv8.Class",
    sizeof(py_class),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    py_class_slots };

int 
py_class_type_init()
{
    py_class_type = (PyTypeObject *)PyType_FromSpec(&py_class_spec);
    if (py_class_type == NULL)
    {
        return -1;
    }
    return 0;
}


void 
py_class_dealloc(py_class* self) 
{
    PyObject_GC_UnTrack(self);
    class_clear(self);
    TP_FREE_ABI3(py_class_type, self);
}

//: The realm's template for a class, keyed by the class's own name.
static PyObject *cached_template(context_c *py_ctx, PyObject *cls, PyObject *name)
{
    PyObject *templ = context_registry_get(py_ctx, name, REGISTRY_TEMPLATE);
    if (templ == nullptr)
        return nullptr;
    if (((py_class *)templ)->cls != cls)
    {
        PyErr_Format(PyExc_ValueError,
                     "JavaScript class name '%U' is already registered in this Context", name);
        return nullptr;
    }
    return owned_template(templ);
}

PyObject *py_class_to_template(PyObject *cls, context_c *py_ctx)
{
    if (py_ctx->registry == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "context interface registry is unavailable");
        return NULL;
    }
    PyObject *name = PyObject_GetAttrString(cls, "__name__");
    if (name == nullptr)
        return nullptr;
    PyObject *templ = cached_template(py_ctx, cls, name);
    if (templ != nullptr || PyErr_Occurred())
    {
        Py_DECREF((PyObject*)name);
        return templ;
    }
    // The factory is where an overlay comes from: ask before the template exists.
    if (context_registry_get(py_ctx, name, REGISTRY_SPEC) == NULL
        && context_registry_askable(py_ctx, name))
    {
        PyObject *answer = context_ask_factory(py_ctx, name);
        if (answer == nullptr)
        {
            Py_DECREF((PyObject*)name);
            return nullptr;
        }
        bool mine = answer == Py_None || answer == cls;
        Py_DECREF((PyObject*)answer);
        if (!mine)
        {
            Py_DECREF((PyObject*)name);
            PyErr_SetString(PyExc_TypeError,
                            "lazy factory answered a different class for the requested type");
            return nullptr;
        }
        templ = cached_template(py_ctx, cls, name);
        if (templ != nullptr || PyErr_Occurred())
        {
            Py_DECREF((PyObject*)name);
            return templ;
        }
    }
    // The factory above records what this realm's spec is; a realm-independent one
    // keeps a single template for the isolate, as Blink's one-per-world does, so a
    // V8 signature made from it accepts a same-origin receiver from any realm.
    const bool shared = py_ctx->isolate_state != nullptr
        && py_ctx->isolate_state->shared_templates_enabled()
        && context_registry_get(py_ctx, name, REGISTRY_SHARED) == Py_True;
    if (shared)
    {
        PyObject *existing = py_ctx->isolate_state->shared_interface(cls);
        if (existing != nullptr)
        {
            PyObject *owned = owned_template(existing);
            if (context_registry_set(py_ctx, name, REGISTRY_TEMPLATE, owned) < 0)
            {
                Py_DECREF(owned);
                Py_DECREF((PyObject*)name);
                return nullptr;
            }
            Py_DECREF((PyObject*)name);
            return owned;
        }
    }
    templ = py_class_new(cls, py_ctx, shared);
    if (templ == nullptr)
    {
        Py_DECREF((PyObject*)name);
        return nullptr;
    }
    if (shared)
        py_ctx->isolate_state->remember_shared_interface(cls, templ);
    if (context_registry_set(py_ctx, name, REGISTRY_TEMPLATE, templ) < 0)
    {
        Py_DECREF((PyObject*)templ);
        Py_DECREF((PyObject*)name);
        return NULL;
    }
    PyObject *installed = owned_template(templ);
    Py_DECREF((PyObject*)templ);
    Py_DECREF((PyObject*)name);
    return installed;
}
void py_class_release_handles(PyObject* templ)
{
    if (templ == nullptr)
        return;
    auto* self = (py_class*)templ;
    self->templ.Reset();
    self->iterator_templ.Reset();
}

int add_to_template(PyObject* cls, context_c* py_ctx, PyObject* member_name, PyObject* member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world);


PyObject *
py_class_new(PyObject *cls, context_c * py_ctx, bool shared)
{
    IsolateState* isolate_state = py_ctx->isolate_state;
    if (isolate_state == nullptr || !isolate_state->require_active())
        return NULL;
    IN_ISOLATE(isolate_state);

    // The convert functions require a context for function conversion, but we
    // don't have a context. So we use an empty context and special-case
    // functions.
    Local<Context> no_ctx;

    py_class *self = (py_class *) PyType_GenericAlloc(py_class_type, 0);
    PyErr_PROPAGATE(self);

    self->isolate_state = isolate_state;
    self->cls = NULL;
    self->idl_spec = NULL;
    self->cls_name = NULL;
    self->operation_callback_data = NULL;
    self->constructor_adapter = NULL;
    self->parent = NULL;
    self->constructor_behavior = 0;
    self->unconstructable = 0;
    self->has_constructor = 0;
    self->to_obj = 0;
    self->error_class = 0;
    self->iterator_prototype = 0;
    self->shares_parent_prototype = 0;
    self->templ.Reset();

    self->operation_callback_data = PyList_New(0);
    if (self->operation_callback_data == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    Py_INCREF((PyObject*)cls);
    self->cls = cls;
    self->cls_name = PyObject_GetAttrString(cls, "__name__");
    if (self->cls_name == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    PyObject* registered = context_registry_get(py_ctx, self->cls_name, REGISTRY_SPEC);
    if (registered != nullptr) Py_INCREF((PyObject*)registered);
    else if (!PyErr_Occurred()) registered = idl_spec_for(cls);
    if (registered != nullptr) {
        self->idl_spec = idl_spec_resolve(registered, cls);
        Py_DECREF((PyObject*)registered);
    }
    if (PyErr_Occurred()) { Py_DECREF((PyObject*)self); return nullptr; }
    IdlSpec defaults;
    const IdlSpec& config = self->idl_spec == nullptr ? defaults : *(IdlSpec*)self->idl_spec;
    Local<External> js_self = shared
        ? isolate_state->shared_callback_data((PyObject*)self)
        : isolate_state->callback_data(py_ctx, (PyObject*)self);
    if (js_self.IsEmpty()) { Py_DECREF((PyObject*)self); return nullptr; }
    int templ_length = (int)config.length;
    self->constructor_behavior = (int)config.constructor_behavior;
    self->shares_parent_prototype = config.shares_parent_prototype != 0;
    self->unconstructable = config.unconstructable;
    self->constructor_adapter = config.constructor_adapter;
    Py_XINCREF(self->constructor_adapter);

    Local<FunctionTemplate> templ = FunctionTemplate::New(isolate, CallbackBoundary<py_class_construct_callback>::call, js_self,
            Local<Signature>(), templ_length, ConstructorBehavior::kAllow);

    const char* world = PyUnicode_AsUTF8_abi3(self->cls_name);
    if (world == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    Local<String> v8_name = V8String(isolate, world);
    Local<String> v8_tag = v8_name;
    if (config.to_string_tag != NULL) {
        const char* tag = PyUnicode_AsUTF8_abi3(config.to_string_tag);
        if (tag == NULL) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        v8_tag = V8String(isolate, tag);
    }

    Local<Signature> sig = Signature::New(isolate, templ);

    if (self->idl_spec == nullptr) {
        PyObject* class_dict = PyObject_GetAttrString(cls, "__dict__");
        PyObject* items = class_dict == nullptr ? nullptr : PyMapping_Items(class_dict);
        Py_XDECREF((PyObject*)class_dict);
        if (items == nullptr) { Py_DECREF((PyObject*)self); return nullptr; }
        for (Py_ssize_t index = 0; index < PyList_Size(items); ++index) {
            PyObject* item = PyList_GetItem(items, index);
            if (PyUnicode_CompareWithASCIIString(PyTuple_GetItem(item, 0), "__call__") == 0) {
                defaults.call = PyTuple_GetItem(item, 1);
                if (PyList_Append(self->operation_callback_data, defaults.call) < 0) {
                    Py_DECREF((PyObject*)items);
                    Py_DECREF((PyObject*)self);
                    return nullptr;
                }
            }
            if (add_to_template(cls, py_ctx, PyTuple_GetItem(item, 0), PyTuple_GetItem(item, 1), templ, sig, world) < 0) {
                Py_DECREF((PyObject*)items);
                Py_DECREF((PyObject*)self);
                return nullptr;
            }
        }
        Py_DECREF((PyObject*)items);
    }

    if (config.attributes != NULL)
    {
        if (!bindings::InstallAttributes(
                isolate, world, templ, sig,
                cls,
                config.attributes,
                self->operation_callback_data, py_ctx, shared)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
        
    if (config.methods != NULL)
    {
        if (!bindings::InstallOperations(
                isolate, world, templ, sig,
                cls,
                config.methods,
                self->operation_callback_data, py_ctx, shared)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }

    if (config.constants_value != NULL &&
        !bindings::InstallConstantValues(
            isolate, world, templ, sig, config.constants_value))
    {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    // After the declared methods, so an iteration source replaces their views.
    if (!install_sync_iterable(isolate, self, js_self, templ, sig))
    {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    if (config.call != NULL)
    {
        PyObject* args = PyTuple_New(0);
        PyObject* kwargs = Py_BuildValue("{s:s,s:O}", "name", "__call__", "callback", config.call);
        PyObject* data = args != nullptr && kwargs != nullptr ? PyObject_Call((PyObject*)method_spec_type, args, kwargs) : nullptr;
        Py_XDECREF((PyObject*)args);
        Py_XDECREF((PyObject*)kwargs);
        if (data == nullptr || PyList_Append(self->operation_callback_data, data) < 0) {
            Py_XDECREF((PyObject*)data);
            Py_DECREF((PyObject*)self);
            return nullptr;
        }
        auto anchor = shared
            ? isolate_state->shared_callback_data(data)
            : isolate_state->callback_data(py_ctx, data);
        if (anchor.IsEmpty()) { Py_DECREF(data); Py_DECREF((PyObject*)self); return nullptr; }
        templ->InstanceTemplate()->SetCallAsFunctionHandler(CallbackBoundary<py_class_method_callback>::call, anchor);
        Py_DECREF((PyObject*)data);
    }

    // V8 requires a callable instance template before an undetectable
    // document.all-style object can be marked as undetectable.
    if (config.undetectable)
    {
        templ->InstanceTemplate()->MarkAsUndetectable();
    }

    // Code-like: eval and the Function constructor stringify such a value.
    if (config.code_like)
    {
        templ->InstanceTemplate()->SetCodeLike();
    }
        
    if (config.immutable != 0)
    {
        int flag = (int)config.immutable;

        if ((flag & FlagImmutable::instance) != 0)
            templ->InstanceTemplate()->SetImmutableProto();
        if ((flag & FlagImmutable::prototype) != 0)
            templ->PrototypeTemplate()->SetImmutableProto();
    }

    if (config.has_constructor != 0)
    {
        self->has_constructor = (int)config.has_constructor;
    }

    if ((py_ctx->traceable|| config.name_get) && !config.named_properties_object)
    {
        auto handler_flags = v8::PropertyHandlerFlags::kNone;
        if (config.named_properties_non_masking)
            handler_flags = v8::PropertyHandlerFlags::kNonMasking;
        if (config.undetectable) {
            handler_flags = static_cast<v8::PropertyHandlerFlags>(
                int32_t(v8::PropertyHandlerFlags::kNonMasking) |
                int32_t(v8::PropertyHandlerFlags::kOnlyInterceptStrings) |
                int32_t(v8::PropertyHandlerFlags::kHasNoSideEffect));
        }
        NamedPropertyHandlerConfiguration callbacks(
            CallbackBoundary<named_getter>::call,
            (py_ctx->traceable|| config.name_get) ? CallbackBoundary<named_setter>::call : nullptr,
            // The query callback answers ``in`` and other existence probes, so a
            // traced context installs it to observe them.  Declining leaves the
            // engine's own lookup in charge, exactly like the getter above.
            (py_ctx->traceable || config.name_query) ? CallbackBoundary<named_query>::call : nullptr,
            config.name_del ? CallbackBoundary<named_deleter>::call : nullptr,
            config.name_enum ? CallbackBoundary<named_enumerator>::call : nullptr,
            CallbackBoundary<named_definer>::call,
            CallbackBoundary<named_descriptor>::call,
            v8::Local<v8::Value>(),
            handler_flags);
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if ((py_ctx->traceable|| config.index_get) && !config.named_properties_object)
    {
        auto handler_flags = v8::PropertyHandlerFlags::kNone;
        if (config.undetectable) {
            handler_flags = v8::PropertyHandlerFlags::kHasNoSideEffect;
        }
        IndexedPropertyHandlerConfiguration callbacks(
            CallbackBoundary<indexed_getter>::call,
            (py_ctx->traceable|| config.index_get) ? CallbackBoundary<indexed_setter>::call : nullptr,
            (py_ctx->traceable || config.index_query) ? CallbackBoundary<indexed_query>::call : nullptr,
            config.index_del ? CallbackBoundary<indexed_deleter>::call : nullptr,
            config.index_enum ? CallbackBoundary<indexed_enumerator>::call : nullptr,
            CallbackBoundary<indexed_definer>::call,
            CallbackBoundary<indexed_descriptor>::call,
            v8::Local<v8::Value>(),
            handler_flags);
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if (config.cross_origin_check)
    {   
        if (config.cross_origin_name_get){
            NamedPropertyHandlerConfiguration cross_origin_named_callbacks(
                CallbackBoundary<cross_origin_named_getter>::call,
                config.cross_origin_name_set ? CallbackBoundary<cross_origin_named_setter>::call : nullptr,
                config.cross_origin_name_query ? CallbackBoundary<cross_origin_named_query>::call : nullptr,
                config.cross_origin_name_del ? CallbackBoundary<cross_origin_named_deleter>::call : nullptr,
                config.cross_origin_name_enum ? CallbackBoundary<cross_origin_named_enumerator>::call : nullptr);

            IndexedPropertyHandlerConfiguration cross_origin_index_callbacks(
                config.cross_origin_index_get ? CallbackBoundary<cross_origin_indexed_getter>::call : nullptr,
                config.cross_origin_index_set ? CallbackBoundary<cross_origin_indexed_setter>::call : nullptr,
                config.cross_origin_index_query ? CallbackBoundary<cross_origin_indexed_query>::call : nullptr,
                config.cross_origin_index_del ? CallbackBoundary<cross_origin_indexed_deleter>::call : nullptr,
                config.cross_origin_index_enum ? CallbackBoundary<cross_origin_indexed_enumerator>::call : nullptr);
            templ->InstanceTemplate()->SetAccessCheckCallbackAndHandler(CallbackBoundary<cross_origin_access_check>::call, cross_origin_named_callbacks, cross_origin_index_callbacks);
        }
        else{
            templ->InstanceTemplate()->SetAccessCheckCallback(CallbackBoundary<cross_origin_access_check>::call);
        }
    }

    if (config.named_properties_object) {
        NamedPropertyHandlerConfiguration name_callbacks(
            CallbackBoundary<properties_named_getter>::call, nullptr,
            CallbackBoundary<properties_named_query>::call, CallbackBoundary<properties_named_deleter>::call,
            CallbackBoundary<properties_named_enumerator>::call, CallbackBoundary<properties_named_definer>::call,
            CallbackBoundary<properties_named_descriptor>::call, Local<Value>(),
            PropertyHandlerFlags::kOnlyInterceptStrings);
        templ->PrototypeTemplate()->SetHandler(name_callbacks);
        IndexedPropertyHandlerConfiguration index_callbacks(
            CallbackBoundary<properties_indexed_getter>::call, nullptr,
            CallbackBoundary<properties_indexed_query>::call, CallbackBoundary<properties_indexed_deleter>::call,
            CallbackBoundary<properties_named_enumerator>::call, CallbackBoundary<properties_indexed_definer>::call,
            CallbackBoundary<properties_indexed_descriptor>::call);
        templ->PrototypeTemplate()->SetHandler(index_callbacks);
        templ->PrototypeTemplate()->SetImmutableProto();
    }

    PyObject* bases = NULL;
    PyObject* last_base = config.parent_class;
    if (last_base == NULL) {
        bases = PyObject_GetAttrString(cls, "__bases__");
        if (bases == NULL)
        {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        //PyObject*  bases = ((PyTypeObject*)cls)->tp_bases;
        last_base = PyTuple_GetItem(bases, PyTuple_Size(bases) - 1);
        if (last_base == NULL) {
            PyErr_Clear();
        }
    }
    Py_XDECREF((PyObject*)bases);

    // A class a realm is shown as a JavaScript error -- one deriving from the error family
    // or from a builtin exception -- inherits %Error.prototype% directly, the way V8's own
    // error prototypes and Chromium's DOMException do.  The Python bases do not show: they
    // are how one ``isinstance`` answers for the whole family.
    const bool error_class =
        last_base == PyExc_Exception || last_base == PyExc_BaseException
        || error_family::family_name((PyTypeObject*)cls) != nullptr;
    self->error_class = error_class ? 1 : 0;

    if (error_class) {
        Local<FunctionTemplate> intrinsic = FunctionTemplate::New(
            isolate, nullptr, Local<Value>(), Local<Signature>(), 0, ConstructorBehavior::kThrow);
        intrinsic->RemovePrototype();
        intrinsic->SetIntrinsicDataProperty(V8String(isolate, "prototype"), Intrinsic::kErrorPrototype);
        templ->Inherit(intrinsic);
    }
    else if (last_base != NULL && last_base != (PyObject*)&PyBaseObject_Type) {
        py_class* superclass_templ = (py_class*)py_class_to_template(last_base, py_ctx);
        if (superclass_templ == NULL) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        templ->Inherit(superclass_templ->templ.Get(isolate));
        self->parent = (PyObject*)superclass_templ;
        Py_DECREF((PyObject*)superclass_templ);
    }

    if (config.definition_kind == kIdlNamespace)
    {
        // A namespace is a plain object in Chrome, not a constructor: Blink builds it from an
        // ObjectTemplate and tags the object itself (SetupIDLNamespaceTemplate).
        self->to_obj = 1;
        templ->Set(Symbol::GetToStringTag(isolate), v8_tag, PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    }
    else if (config.definition_kind == kWrapperTypeNoPrototype)
    {
        // A callback interface answers ``typeof X === 'function'`` with no ``prototype``: its
        // object is a function whose prototype V8 never makes, which is what Blink does for
        // ``callback interface`` in SetupIDLCallbackInterfaceTemplate.
        self->to_obj = 0;
        templ->RemovePrototype();
        templ->SetClassName(v8_name);
    }
    else
    {
        self->to_obj = 0;
        // A legacy factory's ``prototype`` is the interface's own object, so it must stay
        // writable until py_class_get_constructor points it there.
        if (!self->shares_parent_prototype)
            templ->ReadOnlyPrototype();
        templ->SetClassName(v8_name);
        templ->PrototypeTemplate()->Set(Symbol::GetToStringTag(isolate), v8_tag, PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    }

    templ->InstanceTemplate()->SetInternalFieldCount(OBJECT_INTERNAL_FIELDS);

    self->templ.Set(isolate_state, isolate, templ);
    return (PyObject *) self;
}
                                                       

// 0 on success, -1 on failure
int 
add_to_template(PyObject *cls, context_c* py_ctx, PyObject *member_name, PyObject *member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world)
{
    Isolate* isolate = py_ctx->isolate_state->isolate();
    HandleScope hs(isolate);
    Local<Context> no_ctx;
    // An AccessorSignature here would break property access on the global object.
    if (PyUnicode_Check(member_name) && PyUnicode_AsUTF8_abi3(member_name)[0] == '_')
    {
        return 0;
    }

    bool hidden = false;
    if (!idl_spec_flag(member_value, "hidden", &hidden)) return -1;
    if (hidden) {
        return 0;
    }

    if (PyFunction_Check_abi3(member_value)) {
        return 0;
    }
    
    if (PyObject_HasAttrString(member_value, "__get__") && !PyFunction_Check_abi3(member_value)) {
        return 0;
    }

    if (PyLong_Check(member_value)) {
        bindings::ConstantValueConfig config;
        config.name = PyUnicode_AsUTF8_abi3(member_name);
        config.value = PyLong_AsLong(member_value);
        bindings::InstallConstantValue(isolate, world, templ, sig, config);
        return 0;
    }

    Local<Name> js_name = js_from_py(member_name, no_ctx, isolate).As<Name>();
    Local<Data> js_value = js_from_py(member_value, no_ctx, isolate);
    if (!js_value.IsEmpty())
    {
        templ->Set(js_name, js_value);
        templ->PrototypeTemplate()->Set(js_name, js_value);
        return 0;
    }

    // otherwise just convert
    if (PyType_Check(member_value) && std::isupper(static_cast<unsigned char>(PyUnicode_AsUTF8_abi3(member_name)[0])))
    {
        PyObject* member_template = py_class_to_template(member_value, py_ctx);
        if (member_template == nullptr)
            return -1;
        Py_DECREF(member_template);
        return 0;
    }

    return -1;
}


// A namespace (``CSS``) is an object in Blink, not a function: ``GetV8ClassTemplate`` answers a
// ``v8::ObjectTemplate`` for it and ``GetExposedNamespaceObject`` news an instance of that
// (platform/bindings/wrapper_type_info.cc, v8_set_return_value.cc).  This binding stamps an
// interface's members onto a FunctionTemplate, so the object is built here from that function's
// own properties -- the constants and the methods, with the attributes they were given -- plus
// the tag ``SetupIDLNamespaceTemplate`` puts on the template itself.  Nothing else of the
// function comes along: Chrome's namespace has no ``length``, ``name`` or ``prototype``.
static Local<Value>
py_class_namespace_object(Local<Context> context, py_class* self, Local<Function> interface_object)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    Local<Array> names;
    if (!interface_object->GetOwnPropertyNames(context).ToLocal(&names)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to read a namespace's own names");
        return Local<Value>();
    }
    Local<Object> object = Object::New(isolate);
    for (uint32_t index = 0; index < names->Length(); ++index) {
        Local<Value> name_value;
        if (!names->Get(context, index).ToLocal(&name_value) || !name_value->IsString()) {
            continue;
        }
        Local<String> name = name_value.As<String>();
        if (name->Equals(context, V8String(isolate, "length")).FromMaybe(false)
                || name->Equals(context, V8String(isolate, "name")).FromMaybe(false)
                || name->Equals(context, V8String(isolate, "prototype")).FromMaybe(false)) {
            continue;
        }
        Local<Value> described;
        if (!interface_object->GetOwnPropertyDescriptor(context, name).ToLocal(&described)
                || !described->IsObject()) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read a namespace member");
            return Local<Value>();
        }
        Local<Object> descriptor = described.As<Object>();
        Local<Value> getter, setter, value, writable, flag;
        bool accessor = descriptor->Get(context, V8String(isolate, "get")).ToLocal(&getter)
                        && descriptor->Get(context, V8String(isolate, "set")).ToLocal(&setter)
                        && (!getter->IsUndefined() || !setter->IsUndefined());
        if (!accessor
                && (!descriptor->Get(context, V8String(isolate, "value")).ToLocal(&value)
                    || !descriptor->Get(context, V8String(isolate, "writable")).ToLocal(&writable))) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read a namespace member");
            return Local<Value>();
        }
        if (!descriptor->Get(context, V8String(isolate, "enumerable")).ToLocal(&flag)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read a namespace member");
            return Local<Value>();
        }
        bool enumerable = flag->BooleanValue(isolate);
        if (!descriptor->Get(context, V8String(isolate, "configurable")).ToLocal(&flag)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read a namespace member");
            return Local<Value>();
        }
        bool configurable = flag->BooleanValue(isolate);
        bool defined = false;
        if (accessor) {
            PropertyDescriptor property(getter, setter);
            property.set_enumerable(enumerable);
            property.set_configurable(configurable);
            object->DefineProperty(context, name, property).To(&defined);
        }
        else {
            PropertyDescriptor property(value, writable->BooleanValue(isolate));
            property.set_enumerable(enumerable);
            property.set_configurable(configurable);
            object->DefineProperty(context, name, property).To(&defined);
        }
        if (!defined) {
            PyErr_SetString(PyExc_RuntimeError, "failed to define a namespace member");
            return Local<Value>();
        }
    }
    const char* world = PyUnicode_AsUTF8_abi3(self->cls_name);
    if (world == NULL) {
        return Local<Value>();
    }
    // Blink's SetupIDLNamespaceTemplate: read-only and not enumerated, as ``ReadOnly | DontEnum``.
    PropertyDescriptor tag(V8String(isolate, world), false);
    tag.set_enumerable(false);
    tag.set_configurable(true);
    bool tagged = false;
    object->DefineProperty(context, Symbol::GetToStringTag(isolate), tag).To(&tagged);
    if (!tagged) {
        PyErr_SetString(PyExc_RuntimeError, "failed to tag a namespace object");
        return Local<Value>();
    }
    return object;
}

// Blink installs later template members after the initial members, and context-
// dependent members after the constructor. Preserve their original descriptors.
static bool install_object_member_order(Local<Context> context, py_class* self,
                                        Local<Function> function) {
    if (self->idl_spec == nullptr) return true;
    PyObject* members = ((IdlSpec*)self->idl_spec)->prototype_late_members;
    if (members == nullptr || PyTuple_Size(members) == 0) return true;
    Isolate* isolate = v8_compat::context_isolate(context);
    Local<Value> prototype_value;
    if (!function->Get(context, V8String(isolate, "prototype")).ToLocal(&prototype_value)) return false;
    if (!prototype_value->IsObject()) return true;
    Local<Object> prototype = prototype_value.As<Object>();
    Local<Private> marker = Private::ForApi(isolate, V8String(isolate, "pyv8.prototype_late_members"));
    Local<Value> done;
    if (!prototype->GetPrivate(context, marker).ToLocal(&done)) return false;
    if (done->IsTrue()) return true;
    std::vector<Local<String>> names;
    std::vector<Local<Object>> descriptors;
    for (Py_ssize_t i = 0; i < PyTuple_Size(members); ++i) {
        const char* name = PyUnicode_AsUTF8_abi3(PyTuple_GetItem(members, i));
        if (name == nullptr) return false;
        Local<String> key = V8String(isolate, name);
        Local<Value> value;
        if (!prototype->GetOwnPropertyDescriptor(context, key).ToLocal(&value)) return false;
        if (value->IsUndefined()) continue;
        if (!value->IsObject()) return false;
        Local<Object> desc = value.As<Object>();
        Local<Value> configurable;
        if (!desc->Get(context, V8String(isolate, "configurable")).ToLocal(&configurable)) return false;
        // A non-configurable member cannot be moved without changing its semantics.
        if (!configurable->BooleanValue(isolate)) return true;
        names.push_back(key);
        descriptors.push_back(desc);
    }
    for (size_t i = 0; i < names.size(); ++i) {
        bool removed;
        if (!prototype->Delete(context, names[i]).To(&removed) || !removed) return false;
    }
    for (size_t i = 0; i < names.size(); ++i) {
        Local<Object> desc = descriptors[i];
        Local<Value> enumerable, configurable, getter, setter, value, writable;
        if (!desc->Get(context, V8String(isolate, "enumerable")).ToLocal(&enumerable) ||
            !desc->Get(context, V8String(isolate, "configurable")).ToLocal(&configurable)) return false;
        bool accessor;
        if (!desc->HasOwnProperty(context, V8String(isolate, "get")).To(&accessor)) return false;
        if (accessor) {
            if (!desc->Get(context, V8String(isolate, "get")).ToLocal(&getter) ||
                !desc->Get(context, V8String(isolate, "set")).ToLocal(&setter)) return false;
            PropertyDescriptor property(getter, setter);
            property.set_enumerable(enumerable->BooleanValue(isolate));
            property.set_configurable(configurable->BooleanValue(isolate));
            bool defined;
            if (!prototype->DefineProperty(context, names[i], property).To(&defined) || !defined) return false;
        } else {
            if (!desc->Get(context, V8String(isolate, "value")).ToLocal(&value) ||
                !desc->Get(context, V8String(isolate, "writable")).ToLocal(&writable)) return false;
            PropertyDescriptor property(value, writable->BooleanValue(isolate));
            property.set_enumerable(enumerable->BooleanValue(isolate));
            property.set_configurable(configurable->BooleanValue(isolate));
            bool defined;
            if (!prototype->DefineProperty(context, names[i], property).To(&defined) || !defined) return false;
        }
    }
    return prototype->SetPrivate(context, marker, True(isolate)).FromMaybe(false);
}

Local<Value>
py_class_get_constructor(Local<Context> context, py_class* self, py_class* parent)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (self->isolate_state == nullptr || self->isolate_state->isolate() != isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        return Local<Value>();
    }
    Context::Scope cs(context);
    delete_constructor(self, context);
    Local<Function> js_parent;
    if (parent != NULL) {
        bool skip_inherit = parent->idl_spec != nullptr && ((IdlSpec*)parent->idl_spec)->skip_inherit;
        if (skip_inherit)
            parent = (py_class *)parent->parent;

        if (parent != NULL) {
            Local<Value> parent_value = py_class_get_constructor(context, parent, (py_class*)parent->parent);
            if (parent_value.IsEmpty()) {
                return Local<Value>();
            }
            // Nothing inherits from a namespace, so its object is not a prototype to set.
            if (parent_value->IsFunction()) {
                js_parent = parent_value.As<Function>();
            }
        }
    }
    Local<Function> function;
    if (!without_gil([&] { return self->templ.Get(isolate)->GetFunction(context).ToLocal(&function); })) {
        PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript class template");
        return Local<Value>();
    }
    if (self->to_obj) {
        return py_class_namespace_object(context, self, function);
    }
    if (!install_object_member_order(context, self, function)) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_RuntimeError, "failed to order prototype members");
        return Local<Value>();
    }
    // A legacy factory shares its interface's prototype object: Option.prototype IS
    // HTMLOptionElement.prototype, the way Blink installs ``Option``/``Image``/``Audio``
    // (CreateLegacyFactoryFunctionFunction in generated_code_helper.cc).
    if (self->shares_parent_prototype && !js_parent.IsEmpty()) {
        Local<Value> parent_prototype;
        if (!js_parent->Get(context, V8String(isolate, "prototype")).ToLocal(&parent_prototype) ||
            !parent_prototype->IsObject()) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read the interface prototype for a legacy factory");
            return Local<Value>();
        }
        bool defined = false;
        if (!function->DefineOwnProperty(
                context, V8String(isolate, "prototype"), parent_prototype,
                PropertyAttribute(v8::ReadOnly | v8::DontEnum | v8::DontDelete)).To(&defined) ||
            !defined) {
            PyErr_SetString(PyExc_RuntimeError, "failed to share the interface prototype with a legacy factory");
            return Local<Value>();
        }
    }
    if (!js_parent.IsEmpty()) {
        bool set_worked;
        if (!function->SetPrototype(context, js_parent).To(&set_worked)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript class prototype");
            return Local<Value>();
        }
    }
    Local<Value> class_name = js_from_py(self->cls_name, context);
    if (PyErr_Occurred()) {
        return Local<Value>();
    }
    if (!class_name.IsEmpty() && class_name->IsString()) {
        function->SetName(class_name.As<String>());
    }
    return function;
}

bool
py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    auto* owner = context_from_v8(context);
    if (!isolate_state->track_python_object(js_object, py_object, owner)) {
        return false;
    }
    js_object->SetInternalField(
        PYTHON_MAGIC_FIELD,
        isolate_state->v8_string(V8StringId::PythonObjectMagic));
    // The pair is published by track_python_object, which owns it.
    return true;
}

Local<Object> 
py_class_create_js_object(py_class *self, PyObject *py_object, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (self->isolate_state == nullptr || self->isolate_state->isolate() != isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        return Local<Object>();
    }
    EscapableHandleScope hs(isolate);
    context_c* owner_context = context_from_v8(context);
    Local<Object> object = owner_context == nullptr
        ? Local<Object>()
        : self->isolate_state->cached_js_object(owner_context, py_object);
    if (!object.IsEmpty()) {
        return hs.Escape(object);
    }
    if (!without_gil([&] {
            return self->templ.Get(isolate)->InstanceTemplate()->NewInstance(context).ToLocal(&object);
        })) {
        PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript object template");
        return Local<Object>();
    }
    delete_constructor(self, context);
    if (self->idl_spec != nullptr && ((IdlSpec*)self->idl_spec)->prototype_late_members != nullptr &&
        PyTuple_Size(((IdlSpec*)self->idl_spec)->prototype_late_members) > 0) {
        Local<Function> function;
        if (!without_gil([&] { return self->templ.Get(isolate)->GetFunction(context).ToLocal(&function); }) ||
            !install_object_member_order(context, self, function)) {
            if (!PyErr_Occurred()) PyErr_SetString(PyExc_RuntimeError, "failed to order instance prototype members");
            return Local<Object>();
        }
    }
    if (!py_class_init_js_object(object, py_object, context)) {
        return Local<Object>();
    }
    if (self->error_class) {
        // An error the host makes for a realm carries a stack; a page's own ``new X()``
        // does not, which is what Chromium leaves on ``new DOMException()``.  An error
        // rebuilt from the wire asks for none, as the reader Chromium uses builds one.
        if (error_family::carries_stack(py_object))
            error_family::attach_stack(context, object);
    }
    return hs.Escape(object);
}


void 
delete_constructor(py_class* self, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (!self->has_constructor) {
        self->has_constructor = 1;
        v8::Local<v8::FunctionTemplate> interface_template = self->templ.Get(isolate);
        v8::Local<v8::Function> function;
        if (without_gil([&] { return interface_template->GetFunction(context).ToLocal(&function); })) {
            v8::Local<v8::Value> prototype_value;
            if (function->Get(context, V8String(isolate, "prototype")).ToLocal(&prototype_value)) {
                v8::Local<v8::Object> prototype_object = prototype_value.As<v8::Object>();
                properties_delete_constructor(prototype_object, context);
            }
        }
    }

    // An iteration source needs the prototype object, which the constructor guard
    // above does not always reach; finished before a page can look at either.
    if (self->iterator_prototype == 0) {
        self->iterator_prototype = 1;
        install_sync_iterator_prototype(self, context);
    }

    if (self->parent != NULL) {
        delete_constructor((py_class*)self->parent, context);
    }
}
