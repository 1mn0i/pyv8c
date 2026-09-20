#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "pyclass_handlers.h"
#include "idl_spec.h"
#include "pyclass_cross_origin_handlers.h"
#include "v8tools.h"

using namespace v8;


enum FlagIterator {
    iterator = 1 << 0,
    entries = 1 << 1,
    keys = 1 << 2,
    values = 1 << 3,
    forEach = 1 << 4,
};


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

PyObject *py_class_to_template(PyObject *cls, context_c *py_ctx)
{
    if (py_ctx->class_template_cache == NULL)
    {
        PyErr_SetString(PyExc_RuntimeError, "context class template cache is unavailable");
        return NULL;
    }
    PyObject *templ = PyDict_GetItem(py_ctx->class_template_cache, cls);
    if (templ != NULL)
    {
        return owned_template(templ);
    }
    if (py_ctx->lazy_factory != nullptr && PyDict_Contains(py_ctx->class_configs, cls) == 0 &&
        PySet_Contains(py_ctx->resolving_classes, cls) == 0)
    {
        PyObject *name = PyObject_GetAttrString(cls, "__name__");
        if (name == nullptr)
            return nullptr;
        int declared = PySet_Contains(py_ctx->lazy_names, name);
        if (declared < 0)
        {
            Py_DECREF((PyObject*)name);
            return nullptr;
        }
        if (declared == 1)
        {
            if (PySet_Add(py_ctx->resolving_classes, cls) < 0)
            {
                Py_DECREF((PyObject*)name);
                return nullptr;
            }
            PyObject *resolved = PyObject_CallFunctionObjArgs(py_ctx->lazy_factory, name, nullptr);
            Py_DECREF((PyObject*)name);
            PySet_Discard(py_ctx->resolving_classes, cls);
            if (resolved == nullptr)
                return nullptr;
            if (resolved != Py_None && resolved != cls)
            {
                Py_DECREF((PyObject*)resolved);
                PyErr_SetString(PyExc_TypeError,
                                "lazy factory returned a different class for the requested type");
                return nullptr;
            }
            Py_DECREF((PyObject*)resolved);
            templ = PyDict_GetItemWithError(py_ctx->class_template_cache, cls);
            if (templ != nullptr || PyErr_Occurred())
                return owned_template(templ);
        }
        else
            Py_DECREF((PyObject*)name);
    }
    templ = py_class_new(cls, py_ctx);
    PyErr_PROPAGATE(templ);

    if (PyDict_SetItem(py_ctx->class_template_cache, cls, templ) < 0)
    {
        Py_DECREF((PyObject*)templ);
        return NULL;
    }
    PyObject *cls_name = ((py_class *)templ)->cls_name;
    PyObject *existing_name = PyDict_GetItem(py_ctx->class_template_cache, cls_name);
    if (existing_name != NULL && existing_name != templ)
    {
        PyDict_DelItem(py_ctx->class_template_cache, cls);
        Py_DECREF((PyObject*)templ);
        PyErr_Format(PyExc_ValueError, "JavaScript class name '%U' is already registered in this Context",
                     cls_name);
        return NULL;
    }
    if (PyDict_SetItem(py_ctx->class_template_cache, cls_name, templ) < 0)
    {
        PyDict_DelItem(py_ctx->class_template_cache, cls);
        Py_DECREF((PyObject*)templ);
        return NULL;
    }
    Py_DECREF((PyObject*)templ);
    return owned_template(PyDict_GetItem(py_ctx->class_template_cache, cls));
}
int add_to_template(PyObject* cls, context_c* py_ctx, PyObject* member_name, PyObject* member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world);


PyObject *
py_class_new(PyObject *cls, context_c * py_ctx)
{
    IsolateState* isolate_state = py_ctx->isolate_state;
    if (isolate_state == nullptr || !isolate_state->require_active())
        return NULL;
    IN_ISOLATE(isolate_state);

    // The convert functions require a context for function conversion, but we
    // don't have a context. So we use an empty context and special-case
    // functions.
    Local<Context> no_ctx;

    // There used to be an assertion here that the class was not ``object``
    // itself.  It was wrong: a realm for a bare ``object()`` is exactly what a
    // host that wants no interface at all builds, several tests do it, and the
    // assertion only stayed quiet because a release build has no assertions.

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
    self->error_prototype = 0;
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

    PyObject* registered = PyDict_GetItemWithError(py_ctx->class_configs, cls);
    if (registered != nullptr) Py_INCREF((PyObject*)registered);
    else if (!PyErr_Occurred()) registered = idl_spec_for(cls);
    if (registered != nullptr) {
        self->idl_spec = idl_spec_resolve(registered, cls);
        Py_DECREF((PyObject*)registered);
    }
    if (PyErr_Occurred()) { Py_DECREF((PyObject*)self); return nullptr; }
    IdlSpec defaults;
    const IdlSpec& config = self->idl_spec == nullptr ? defaults : *(IdlSpec*)self->idl_spec;
    Local<External> js_self = isolate_state->callback_data(py_ctx, (PyObject*)self);
    if (js_self.IsEmpty()) { Py_DECREF((PyObject*)self); return nullptr; }
    int templ_length = (int)config.length;
    self->constructor_behavior = (int)config.constructor_behavior;
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
                self->operation_callback_data, py_ctx)) {
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
                self->operation_callback_data, py_ctx)) {
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
        auto anchor = isolate_state->callback_data(py_ctx, data);
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
        
    if (config.array != 0)
    {
        int flag = (int)config.array;

        if ((flag & FlagIterator::iterator) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(v8::Symbol::GetIterator(isolate), v8::kArrayProto_values, v8::DontEnum);
        if ((flag & FlagIterator::entries) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "entries"), v8::kArrayProto_entries, v8::None);
        if ((flag & FlagIterator::keys) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "keys"), v8::kArrayProto_keys, v8::None);
        if ((flag & FlagIterator::values) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "values"), v8::kArrayProto_values, v8::None);
        if ((flag & FlagIterator::forEach) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "forEach"), v8::kArrayProto_forEach, v8::None);
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

    if (last_base == PyExc_Exception || last_base == PyExc_BaseException) {
        // A class extending Exception gets Error.prototype.  The marker below is
        // what resolve_error_prototype reads; it has to be a private property
        // because a template cannot carry an internal field, only an object can.
        templ->PrototypeTemplate()->SetPrivate(
            v8::Private::ForApi(isolate, JSTR("__proto__")),
            isolate_state->v8_string(V8StringId::ErrorPrototypeMagic));
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

    if (config.as_object)
    {
        self->to_obj = 1;
        templ->Set(Symbol::GetToStringTag(isolate), v8_tag, PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    }
    else
    {
        self->to_obj = 0;
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


Local<Function> 
py_class_get_constructor(Local<Context> context, py_class* self, py_class* parent)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (self->isolate_state == nullptr || self->isolate_state->isolate() != isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate; use pyv8.transfer() to copy it");
        return Local<Function>();
    }
    Context::Scope cs(context);
    delete_constructor(self, context);
    Local<Function> js_parent;
    if (parent != NULL) {
        bool skip_inherit = parent->idl_spec != nullptr && ((IdlSpec*)parent->idl_spec)->skip_inherit;
        if (skip_inherit)
            parent = (py_class *)parent->parent;

        if (parent != NULL) {
            js_parent = py_class_get_constructor(context, parent, (py_class*)parent->parent);
            if (js_parent.IsEmpty()) {
                return Local<Function>();
            }
        }
    }
    Local<Function> function;
    if (!without_gil([&] { return self->templ.Get(isolate)->GetFunction(context).ToLocal(&function); })) {
        PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript class template");
        return Local<Function>();
    }
    if (!js_parent.IsEmpty()) {
        bool set_worked;
        if (!function->SetPrototype(context, js_parent).To(&set_worked)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript class prototype");
            return Local<Function>();
        }
    }
    Local<Value> class_name = js_from_py(self->cls_name, context);
    if (PyErr_Occurred()) {
        return Local<Function>();
    }
    if (!class_name.IsEmpty() && class_name->IsString()) {
        function->SetName(class_name.As<String>());
    }
    return function;
}

// Does this class stand for a JavaScript Error, and if so give its prototype
// Error.prototype.  Answers 1, or -1 for no, or 0 when it could not be told (with
// the Python error set).  The answer belongs to the class, so the caller caches it.
static int
resolve_error_prototype(Local<Object> js_object, Local<Context> context,
                        Isolate* isolate, IsolateState* isolate_state)
{
    Local<Value> last_proto = js_object;
    Local<Object> last_proto_object;
    while (!last_proto->StrictEquals(context->GetEmbedderData(OBJECT_PROTOTYPE_SLOT))) {
        last_proto_object = last_proto.As<Object>();
        last_proto = last_proto_object->GetPrototype();
    }
    // last_proto is guaranteed to have a value because the loop is guaranteed
    // to run at least once because last_proto_chain is initialized to js_object
    assert(!last_proto.IsEmpty());
    Local<Value> proto_override;
    if (!last_proto_object->GetPrivate(context, v8::Private::ForApi(isolate, JSTR("__proto__"))).ToLocal(&proto_override)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to read JavaScript prototype marker");
        return 0;
    }
    if (!proto_override->StrictEquals(
            isolate_state->v8_string(V8StringId::ErrorPrototypeMagic)))
        return -1;
    bool delete_worked;
    if (!last_proto_object->DeletePrivate(context, v8::Private::ForApi(isolate, JSTR("__proto__"))).To(&delete_worked)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to clear JavaScript prototype marker");
        return 0;
    }
    bool set_worked;
    if (!last_proto_object->SetPrototype(context, context->GetEmbedderData(ERROR_PROTOTYPE_SLOT)).To(&set_worked)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript error prototype");
        return 0;
    }
    return 1;
}

bool
py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context,
                        py_class* self)
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
    if (self == nullptr || self->error_prototype == 0) {
        const int error_prototype = resolve_error_prototype(js_object, context, isolate, isolate_state);
        if (error_prototype == 0)
            return false;
        if (self != nullptr)
            self->error_prototype = error_prototype;
    }
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
    if (!py_class_init_js_object(object, py_object, context, self)) {
        return Local<Object>();
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

    if (self->parent != NULL) {
        delete_constructor((py_class*)self->parent, context);
    }
}
