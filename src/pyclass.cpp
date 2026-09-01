#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "pyclass_config.h"
#include "pyclass_cross_origin_handlers.h"
#include "v8tools.h"
#include "faker/Faker.h"

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

static PyType_Slot py_class_slots[] = {
    {Py_tp_dealloc, (void*)py_class_dealloc},
    {Py_tp_doc, (void*)"v8 Class"},
    {0, NULL} };

static PyType_Spec py_class_spec = {
    "_pyv8.Class",
    sizeof(py_class),
    0,
    Py_TPFLAGS_DEFAULT,
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
    self->templ.Reset();
    Py_XDECREF(self->cls);
    Py_XDECREF(self->cls_name);
    Py_XDECREF(self->call_method);
    Py_XDECREF(self->operation_callback_data);
    Py_XDECREF(self->constructor_adapter);
    //Py_XDECREF(self->parent);
    //Py_TYPE(self)->tp_free((PyObject*)self);
    TP_FREE_ABI3(py_class_type, self);
}

PyObject *
py_class_to_template(PyObject *cls, context_c* py_ctx)
{
    if (py_ctx->class_template_cache == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "context class template cache is unavailable");
        return NULL;
    }
    PyObject *templ = PyDict_GetItem(py_ctx->class_template_cache, cls);
    if (templ != NULL) {
        return templ;
    }
    templ = py_class_new(cls, py_ctx);
    PyErr_PROPAGATE(templ);

    if (PyDict_SetItem(py_ctx->class_template_cache, cls, templ) < 0) {
        Py_DECREF(templ);
        return NULL;
    }
    PyObject* cls_name = ((py_class*)templ)->cls_name;
    PyObject* existing_name = PyDict_GetItem(py_ctx->class_template_cache, cls_name);
    if (existing_name != NULL && existing_name != templ) {
        PyDict_DelItem(py_ctx->class_template_cache, cls);
        Py_DECREF(templ);
        PyErr_Format(PyExc_ValueError,
                     "JavaScript class name '%U' is already registered in this Context",
                     cls_name);
        return NULL;
    }
    if (PyDict_SetItem(py_ctx->class_template_cache, cls_name, templ) < 0) {
        PyDict_DelItem(py_ctx->class_template_cache, cls);
        Py_DECREF(templ);
        return NULL;
    }
    Py_DECREF(templ);
    return PyDict_GetItem(py_ctx->class_template_cache, cls);
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

    assert(cls != (PyObject *) &PyBaseObject_Type);

    py_class *self = (py_class *) PyType_GenericAlloc(py_class_type, 0);
    PyErr_PROPAGATE(self);

    self->isolate_state = isolate_state;
    self->cls = NULL;
    self->cls_name = NULL;
    self->call_method = NULL;
    self->operation_callback_data = NULL;
    self->constructor_adapter = NULL;
    self->parent = NULL;
    self->constructor_behavior = 0;
    self->unconstructable = 0;
    self->has_constructor = 0;
    self->to_obj = 0;
    self->templ.Reset();

    self->operation_callback_data = PyList_New(0);
    if (self->operation_callback_data == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    Py_INCREF(cls);
    self->cls = cls;
    self->cls_name = PyObject_GetAttrString(cls, "__name__");
    if (self->cls_name == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    ClassConfig config;
    if (!class_config_load(cls, py_ctx, &config)) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }

    Local<External> js_self = v8_compat::external_new(isolate, self);
    int templ_lenght = 0;
    if (config.length != NULL) {
        if (!PyLong_Check(config.length)) {
            PyErr_SetString(PyExc_TypeError, "__v8_length__ must be an integer");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        templ_lenght = PyLong_AsLong(config.length);
        if (PyErr_Occurred()) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
    if (config.constructor_behavior != NULL) {
        if (!PyLong_Check(config.constructor_behavior)) {
            PyErr_SetString(PyExc_TypeError, "__v8_constructor__ must be an integer");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        self->constructor_behavior = PyLong_AsLong(config.constructor_behavior);
        if (PyErr_Occurred()) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
    if (config.constructor_adapter != NULL) {
        if (!PyCallable_Check(config.constructor_adapter)) {
            PyErr_SetString(PyExc_TypeError, "__v8_constructor_adapter__ must be callable");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        Py_INCREF(config.constructor_adapter);
        self->constructor_adapter = config.constructor_adapter;
    }
    self->unconstructable = config.unconstructable;

    Local<FunctionTemplate> templ = FunctionTemplate::New(isolate, py_class_construct_callback, js_self,
            Local<Signature>(), templ_lenght, ConstructorBehavior::kAllow);
    
    const char* world = PyUnicode_AsUTF8_abi3(self->cls_name);
    if (world == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    Local<String> v8_name = V8String(isolate, world);
    Local<String> v8_tag = v8_name;
    if (config.to_string_tag != NULL) {
        if (!PyUnicode_Check(config.to_string_tag)) {
            PyErr_SetString(PyExc_TypeError, "__v8_to_string_tag__ must be a string");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        const char* tag = PyUnicode_AsUTF8_abi3(config.to_string_tag);
        if (tag == NULL) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        v8_tag = V8String(isolate, tag);
    }

    Local<Signature> sig = Signature::New(isolate, templ);
    if (config.global_this) {
        Faker::Register(isolate, templ);
    }

    PyObject* keys = PyMapping_Keys(config.class_dict);
    if (!keys) {
        Py_DECREF((PyObject*)self);
        return NULL; 
    }
    Py_ssize_t num_keys = PyList_Size(keys);
    for (Py_ssize_t i = 0; i < num_keys; i++) 
    {
        PyObject* member_name = PyList_GetItem(keys, i);
        PyObject* member_value = PyObject_GetItem(config.class_dict, member_name);
        if (member_value == NULL) {
            Py_DECREF(keys);
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        if (add_to_template(cls, py_ctx, member_name, member_value, templ, sig, world) < 0) {
            Py_DECREF(member_value);
            Py_DECREF(keys);
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        Py_DECREF(member_value);
    }
    Py_XDECREF(keys);

    if (config.attributes != NULL)
    {
        if (!bindings::InstallAttributes(
                isolate, world, templ, sig,
                config.callback_owner != NULL ? config.callback_owner : cls,
                config.attributes,
                self->operation_callback_data)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }
        
    if (config.methods != NULL)
    {
        if (!bindings::InstallOperations(
                isolate, world, templ, sig,
                config.callback_owner != NULL ? config.callback_owner : cls,
                config.methods,
                self->operation_callback_data)) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }

    if (config.constant_values != NULL &&
        !bindings::InstallConstantValues(
            isolate, world, templ, sig, config.constant_values))
    {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
        
    if (config.constant_callbacks != NULL &&
        !bindings::InstallConstantCallbacks(
            isolate, world, templ, sig, config.constant_callbacks))
    {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
        
    if (config.call != NULL)
    {
        Py_INCREF(config.call);
        self->call_method = config.call;
        templ->InstanceTemplate()->SetCallAsFunctionHandler(py_class_method_callback, v8_compat::external_new(isolate, self->call_method));
    }

    // V8 requires a callable instance template before an undetectable
    // document.all-style object can be marked as undetectable.
    if (config.undetectable)
    {
        templ->InstanceTemplate()->MarkAsUndetectable();
    }
        
    if (config.array != NULL)
    {
        if (!PyLong_Check(config.array)) {
            PyErr_SetString(PyExc_TypeError, "__v8_array__ must be an integer");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        int flag = PyLong_AsLong(config.array);
        if (PyErr_Occurred()) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }

        if ((flag & FlagIterator::iterator) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(v8::Symbol::GetIterator(isolate), v8::kArrayProto_values, v8::DontEnum);
        if ((flag & FlagIterator::entries) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "entries"), v8::kArrayProto_entries, v8::None);
        if ((flag & FlagIterator::keys) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "keys"), v8::kArrayProto_entries, v8::None);
        if ((flag & FlagIterator::values) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "values"), v8::kArrayProto_entries, v8::None);
        if ((flag & FlagIterator::forEach) != 0)
            templ->PrototypeTemplate()->SetIntrinsicDataProperty(V8String(isolate, "forEach"), v8::kArrayProto_entries, v8::None);
    }

    if (config.immutable != NULL)
    {
        if (!PyLong_Check(config.immutable)) {
            PyErr_SetString(PyExc_TypeError, "__v8_immutable__ must be an integer");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        int flag = PyLong_AsLong(config.immutable);
        if (PyErr_Occurred()) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }

        if ((flag & FlagImmutable::instance) != 0)
            templ->InstanceTemplate()->SetImmutableProto();
        if ((flag & FlagImmutable::prototype) != 0)
            templ->PrototypeTemplate()->SetImmutableProto();
    }

    if (config.has_constructor != NULL)
    {
        if (!PyLong_Check(config.has_constructor)) {
            PyErr_SetString(PyExc_TypeError, "__v8_has_constructor__ must be an integer");
            Py_DECREF((PyObject*)self);
            return NULL;
        }
        self->has_constructor = PyLong_AsLong(config.has_constructor);
        if (PyErr_Occurred()) {
            Py_DECREF((PyObject*)self);
            return NULL;
        }
    }

    if ((py_ctx->hook || config.name_get) && strcmp(world, "WindowProperties") != 0)
    {
        auto handler_flags = v8::PropertyHandlerFlags::kNone;
        if (config.undetectable) {
            handler_flags = static_cast<v8::PropertyHandlerFlags>(
                int32_t(v8::PropertyHandlerFlags::kNonMasking) |
                int32_t(v8::PropertyHandlerFlags::kOnlyInterceptStrings) |
                int32_t(v8::PropertyHandlerFlags::kHasNoSideEffect));
        }
        NamedPropertyHandlerConfiguration callbacks(
            named_getter,
            (py_ctx->hook || config.name_set) ? named_setter : nullptr,
            config.name_query ? named_query : nullptr,
            config.name_del ? named_deleter : nullptr,
            config.name_enum ? named_enumerator : nullptr,
            named_definer,
            named_descriptor,
            v8::Local<v8::Value>(),
            handler_flags);
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if ((py_ctx->hook || config.index_get) && strcmp(world, "WindowProperties") != 0)
    {
        auto handler_flags = v8::PropertyHandlerFlags::kNone;
        if (config.undetectable) {
            handler_flags = v8::PropertyHandlerFlags::kHasNoSideEffect;
        }
        IndexedPropertyHandlerConfiguration callbacks(
            indexed_getter,
            (py_ctx->hook || config.index_set) ? indexed_setter : nullptr,
            config.index_query ? indexed_query : nullptr,
            config.index_del ? indexed_deleter : nullptr,
            config.index_enum ? indexed_enumerator : nullptr,
            indexed_definer,
            indexed_descriptor,
            v8::Local<v8::Value>(),
            handler_flags);
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if (config.cross_origin_check)
    {   
        if (config.cross_origin_name_get){
            NamedPropertyHandlerConfiguration cross_origin_named_callbacks(
                cross_origin_named_getter,
                config.cross_origin_name_set ? cross_origin_named_setter : nullptr,
                config.cross_origin_name_query ? cross_origin_named_query : nullptr,
                config.cross_origin_name_del ? cross_origin_named_deleter : nullptr,
                config.cross_origin_name_enum ? cross_origin_named_enumerator : nullptr);

            IndexedPropertyHandlerConfiguration cross_origin_index_callbacks(
                config.cross_origin_index_get ? cross_origin_indexed_getter : nullptr,
                config.cross_origin_index_set ? cross_origin_indexed_setter : nullptr,
                config.cross_origin_index_query ? cross_origin_indexed_query : nullptr,
                config.cross_origin_index_del ? cross_origin_indexed_deleter : nullptr,
                config.cross_origin_index_enum ? cross_origin_indexed_enumerator : nullptr);
            templ->InstanceTemplate()->SetAccessCheckCallbackAndHandler(cross_origin_access_check, cross_origin_named_callbacks, cross_origin_index_callbacks);
        }
        else{
            templ->InstanceTemplate()->SetAccessCheckCallback(cross_origin_access_check);
        }
    }

    if (strcmp(world, "WindowProperties") == 0) {
        NamedPropertyHandlerConfiguration name_callbacks(properties_named_getter);
        templ->PrototypeTemplate()->SetHandler(name_callbacks);
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
    Py_XDECREF(bases);

    if (last_base == PyExc_Exception || last_base == PyExc_BaseException) {
        // if the superclass is Exception, ignore that fact and make the superclass Error
        // we do this by putting a magic internal field count on the prototype which py_class_init_js_object detects
        // and sets the prototype's prototype to Error.prototype, which it finds in an embedder data slot on the context
        // it has to be a magic count instead of a field because you can't set an internal field on a template
        // only an object
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
    }

    if (config.as_object)
    {
        self->to_obj = 1;
        //v8::Local<v8::ObjectTemplate> namespace_object_template = templ.As<v8::ObjectTemplate>();
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

    self->templ.Reset(isolate, templ);
    return (PyObject *) self;
}
                                                       

bool 
if_property_has(PyObject *property, const char *what)
{
    if (!PyObject_TypeCheck(property, &PyProperty_Type))
        return true;
    PyObject *value = PyObject_GetAttrString(property, what);
    Py_XDECREF(value);
    return value == Py_None;
}

// 0 on success, -1 on failure
int 
add_to_template(PyObject *cls, context_c* py_ctx, PyObject *member_name, PyObject *member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world)
{
    Isolate* isolate = py_ctx->isolate_state->isolate();
    HandleScope hs(isolate);
    Local<Context> no_ctx;
    // I can't use an AccessorSignature here because then property access on
    // the global object from this gets fucked up.
    if (PyUnicode_Check(member_name) && PyUnicode_AsUTF8_abi3(member_name)[0] == '_')
    {
        return 0;
    }

    if (PyObject_HasAttrString(member_value, "__v8_hidden__")) {
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
        py_class_to_template(member_value, py_ctx);
        return 0;
    }

    return -1;
}


Local<Function> 
py_class_get_constructor(Local<Context> context, py_class* self, py_class* parent)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (self->isolate_state == nullptr || self->isolate_state->isolate() != isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return Local<Function>();
    }
    Context::Scope cs(context);
    delete_constructor(self, context);
    Local<Function> js_parent;
    if (parent != NULL) {
        if(PyObject_HasAttrString(parent->cls, "__v8_skip_inherit__"))
            parent = (py_class *)parent->parent;

        if (parent != NULL) {
            js_parent = py_class_get_constructor(context, parent, (py_class*)parent->parent);
            if (js_parent.IsEmpty()) {
                return Local<Function>();
            }
        }
    }
    Local<Function> function;
    if (!self->templ.Get(isolate)->GetFunction(context).ToLocal(&function)) {
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

bool
py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    if (!isolate_state->track_python_object(js_object, py_object)) {
        return false;
    }
    js_object->SetInternalField(
        PYTHON_MAGIC_FIELD,
        isolate_state->v8_string(V8StringId::PythonObjectMagic));
    js_object->SetInternalField(PYTHON_OBJECT_FIELD, v8_compat::external_new(isolate, py_object));
    js_object->SetInternalField(PYTHON_EXCEPTION_TYPE_FIELD, v8_compat::external_new(isolate, nullptr));
    js_object->SetInternalField(PYTHON_TRACEBACK_FIELD, v8_compat::external_new(isolate, nullptr));
    // find out if the object is supposed to inherit from Error 
    // the information is in an internal field on the last prototype
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
        return false;
    }
    if (proto_override->StrictEquals(
            isolate_state->v8_string(V8StringId::ErrorPrototypeMagic))) {
        bool delete_worked;
        if (!last_proto_object->DeletePrivate(context, v8::Private::ForApi(isolate, JSTR("__proto__"))).To(&delete_worked)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to clear JavaScript prototype marker");
            return false;
        }
        bool set_worked;
        if (!last_proto_object->SetPrototype(context, context->GetEmbedderData(ERROR_PROTOTYPE_SLOT)).To(&set_worked)) {
            PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript error prototype");
            return false;
        }
    }
    context_set_cached_jsobject(context, py_object, js_object);
    if (PyErr_Occurred()) {
        return false;
    }
    return true;
}

Local<Object> 
py_class_create_js_object(py_class *self, PyObject *py_object, Local<Context> context)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    if (self->isolate_state == nullptr || self->isolate_state->isolate() != isolate) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript value belongs to another isolate");
        return Local<Object>();
    }
    EscapableHandleScope hs(isolate);
    Local<Object> object = context_get_cached_jsobject(context, py_object);
    if (!object.IsEmpty()) {
        return hs.Escape(object);
    }
    if (PyErr_Occurred())
        return Local<Object>();
    if (!self->templ.Get(isolate)->InstanceTemplate()->NewInstance(context).ToLocal(&object)) {
        PyErr_SetString(PyExc_RuntimeError, "failed to instantiate JavaScript object template");
        return Local<Object>();
    }
    delete_constructor(self, context);
    if (!py_class_init_js_object(object, py_object, context)) {
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
        if (interface_template->GetFunction(context).ToLocal(&function)) {
            v8::Local<v8::Value> prototype_value;
            if (function->Get(context, V8String(isolate, "prototype")).ToLocal(&prototype_value)) {
                v8::Local<v8::Object> prototype_object = prototype_value.As<v8::Object>();
                bool delete_worked;
                prototype_object
                    ->Delete(
                        context,
                        self->isolate_state->v8_string(V8StringId::Constructor))
                    .To(&delete_worked);
            }
        }
    }

    if (self->parent != NULL) {
        delete_constructor((py_class*)self->parent, context);
    }
}
