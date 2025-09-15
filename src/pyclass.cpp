#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "v8tools.h"
#include "faker/Faker.h"


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
    {Py_tp_doc, "v8 Class"},
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
    Py_XDECREF(self->cls);
    Py_XDECREF(self->cls_name);
    //Py_XDECREF(self->parent);
    self->templ.Reset();
    //Py_TYPE(self)->tp_free((PyObject*)self);
    TP_FREE_ABI3(py_class_type, self);
}

PyObject *cls_template_dict = NULL;

PyObject *
py_class_to_template(PyObject *cls, context_c* py_ctx)
{
    if (cls_template_dict == NULL) {
        cls_template_dict = PyDict_New();
        PyErr_PROPAGATE(cls_template_dict);
    }
    PyObject *templ = PyDict_GetItem(cls_template_dict, cls);
    if (templ != NULL) {
        // PyDict_GetItem returns a borrowed reference
        Py_INCREF(templ);
        return templ;
    }
    templ = py_class_new(cls, py_ctx);
    PyErr_PROPAGATE(templ);

    //PyObject_SetAttrString(cls, "ctx", (PyObject*)py_ctx);
    if (PyDict_SetItem(cls_template_dict, cls, templ) < 0) {
        return NULL;
    }
   /* if (PyDict_SetItem(cls_template_dict, PyObject_GetAttrString(cls, "__name__"), templ) < 0) {
        return NULL;
    }*/
    return templ;
}

int add_to_template(PyObject* cls, context_c* py_ctx, PyObject* member_name, PyObject* member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world);


PyObject *
py_class_new(PyObject *cls, context_c * py_ctx)
{
    IN_V8;

    // The convert functions require a context for function conversion, but we
    // don't have a context. So we use an empty context and special-case
    // functions.
    Local<Context> no_ctx;

    assert(cls != (PyObject *) &PyBaseObject_Type);

    py_class *self = (py_class *) PyType_GenericAlloc(py_class_type, 0);
    PyErr_PROPAGATE(self);

    Py_INCREF(cls);
    self->cls = cls;
    self->cls_name = PyObject_GetAttrString(cls, "__name__");
    if (self->cls_name == NULL) {
        Py_XDECREF(self->cls);
        return NULL;
    }

    //PyObject* class_dict = ((PyTypeObject*)cls)->tp_dict;
    //PyObject* class_dict = PyType_GetDict((PyTypeObject *)cls);
    PyObject* class_dict = PyObject_GetAttrString(cls, "__dict__");  // mappingproxy
    if (class_dict == NULL)
    {
        Py_XDECREF(self->cls);
        Py_XDECREF(self->cls_name);
        return NULL;
    }

    Local<External> js_self = External::New(isolate, self);
    int templ_lenght = 0;
    if (PyMapping_HasKeyString(class_dict, "__v8_length__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_length__");
        if (PyLong_Check(value)) 
            templ_lenght = PyLong_AsLong(value);
        Py_XDECREF(value);
    }

    Local<FunctionTemplate> templ = FunctionTemplate::New(isolate, py_class_construct_callback, js_self,
            Local<Signature>(), templ_lenght, ConstructorBehavior::kAllow);
    
    const char* world = PyUnicode_AsUTF8_abi3(self->cls_name);
    Local<String> v8_name = V8String(isolate, world);

    Local<Signature> sig = Signature::New(isolate, templ);
    if (PyMapping_HasKeyString(class_dict, "__v8_global_this__")) {
        Faker::Register(isolate, templ);
    }

    PyObject* keys = PyMapping_Keys(class_dict);
    if (!keys) {
        return NULL; 
    }
    Py_ssize_t num_keys = PyList_Size(keys);
    for (Py_ssize_t i = 0; i < num_keys; i++) 
    {
        PyObject* member_name = PyList_GetItem(keys, i);
        PyObject* member_value = PyObject_GetItem(class_dict, member_name);
        add_to_template(cls, py_ctx, member_name, member_value, templ, sig, world);
        Py_DECREF(member_value);
    }
    Py_XDECREF(keys);

    if (PyMapping_HasKeyString(class_dict, "__v8_attribute__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_attribute__");
        bindings::InstallAttributes(isolate, world, templ, sig, cls, value);
        Py_DECREF(value);
    }
        
    if (PyMapping_HasKeyString(class_dict, "__v8_method__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_method__");
        bindings::InstallOperations(isolate, world, templ, sig, cls, value);
        Py_DECREF(value);
    }

    if (PyMapping_HasKeyString(class_dict, "__v8_constants_value__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_constants_value__");
        bindings::InstallConstantValues(isolate, world, templ, sig, value);
        Py_DECREF(value);
    }
        
    if (PyMapping_HasKeyString(class_dict, "__v8_constants_callback__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_constants_callback__");
        bindings::InstallConstantCallbacks(isolate, world, templ, sig, value);
        Py_DECREF(value);
    }
        
    if (PyMapping_HasKeyString(class_dict, "__call__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__call__");
        templ->InstanceTemplate()->SetCallAsFunctionHandler(py_class_method_callback, External::New(isolate, value));
        Py_DECREF(value);
    }
        
    if (PyMapping_HasKeyString(class_dict, "__v8_undetectable__"))
    {
        templ->InstanceTemplate()->MarkAsUndetectable();
    }
        
    if (PyMapping_HasKeyString(class_dict, "__v8_array__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_array__");
        int flag = PyLong_AsLong(value);
        Py_DECREF(value);

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

    if (PyMapping_HasKeyString(class_dict, "__v8_immutable__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_immutable__");
        int flag = PyLong_AsLong(value);
        Py_DECREF(value);

        if ((flag & FlagImmutable::instance) != 0)
            templ->InstanceTemplate()->SetImmutableProto();
        if ((flag & FlagImmutable::prototype) != 0)
            templ->PrototypeTemplate()->SetImmutableProto();
    }

    if (PyMapping_HasKeyString(class_dict, "__v8_has_constructor__"))
    {
        PyObject* value = PyMapping_GetItemString(class_dict, "__v8_has_constructor__");
        self->has_constructor = PyLong_AsLong(value);
        Py_DECREF(value);
    }

    if ((py_ctx->hook || PyMapping_HasKeyString(class_dict, "__v8_name_get__")) && strcmp(world, "WindowProperties") != 0)
    {
        NamedPropertyHandlerConfiguration callbacks(named_getter);
        if (py_ctx->hook || PyMapping_HasKeyString(class_dict, "__v8_name_set__")) {
            callbacks.setter = named_setter;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_name_query__") != NULL) {
            callbacks.query = named_query;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_name_enum__") != NULL) {
            callbacks.enumerator = named_enumerator;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_name_del__") != NULL) {
            callbacks.deleter = named_deleter;
        }
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if ((py_ctx->hook || PyMapping_HasKeyString(class_dict, "__v8_index_get__")) && strcmp(world, "WindowProperties") != 0)
    {
        IndexedPropertyHandlerConfiguration callbacks;
        callbacks.getter = indexed_getter;
        if (py_ctx->hook || PyMapping_HasKeyString(class_dict, "__v8_index_set__")) {
            callbacks.setter = indexed_setter;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_index_enum__")) {
            callbacks.enumerator = indexed_enumerator;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_index_query__")) {
            callbacks.query = indexed_query;
        }
        if (PyMapping_HasKeyString(class_dict, "__v8_index_del__")) {
            callbacks.deleter = indexed_deleter;
        }
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_check__"))
    {   
        if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_name_get__")){
            NamedPropertyHandlerConfiguration cross_origin_named_callbacks(cross_origin_named_getter);
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_name_set__") != NULL)
                cross_origin_named_callbacks.setter = cross_origin_named_setter;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_name_query__") != NULL)
                cross_origin_named_callbacks.query = cross_origin_named_query;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_name_del__") != NULL)
                cross_origin_named_callbacks.deleter = cross_origin_named_deleter;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_name_enum__") != NULL)
                cross_origin_named_callbacks.enumerator = cross_origin_named_enumerator;

            IndexedPropertyHandlerConfiguration cross_origin_index_callbacks;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_index_get__") != NULL)
                cross_origin_index_callbacks.getter = cross_origin_indexed_getter;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_index_set__") != NULL)
                cross_origin_index_callbacks.setter = cross_origin_indexed_setter;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_index_query__") != NULL)
                cross_origin_index_callbacks.query = cross_origin_indexed_query;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_index_enum__") != NULL)
                cross_origin_index_callbacks.enumerator = cross_origin_indexed_enumerator;
            if (PyMapping_HasKeyString(class_dict, "__v8_cross_origin_index_del__") != NULL)
                cross_origin_index_callbacks.deleter = cross_origin_indexed_deleter;
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

    PyObject* bases = PyObject_GetAttrString(cls, "__bases__");
    if (bases == NULL)
    {
        return NULL;
    }
    //PyObject*  bases = ((PyTypeObject*)cls)->tp_bases;
    PyObject* last_base = PyTuple_GetItem(bases, PyTuple_Size(bases) - 1);
    if (last_base == NULL) {
        PyErr_Clear();
    }
    Py_XDECREF(bases);

    if (last_base == PyExc_Exception || last_base == PyExc_BaseException) {
        // if the superclass is Exception, ignore that fact and make the superclass Error
        // we do this by putting a magic internal field count on the prototype which py_class_init_js_object detects
        // and sets the prototype's prototype to Error.prototype, which it finds in an embedder data slot on the context
        // it has to be a magic count instead of a field because you can't set an internal field on a template
        // only an object
        templ->PrototypeTemplate()->SetPrivate(v8::Private::ForApi(isolate, JSTR("__proto__")), I_CAN_HAZ_ERROR_PROTOTYPE);
        //templ->PrototypeTemplate()->Set(JSTR("__proto__"), I_CAN_HAZ_ERROR_PROTOTYPE);
    }
    else if (last_base != NULL && last_base != (PyObject*)&PyBaseObject_Type) {
        py_class* superclass_templ = (py_class*)py_class_to_template(last_base, py_ctx);
        templ->Inherit(superclass_templ->templ.Get(isolate));
        self->parent = (PyObject*)superclass_templ;
    }

    if (PyMapping_HasKeyString(class_dict, "__v8_as_obj__"))
    {
        self->to_obj = 1;
        //v8::Local<v8::ObjectTemplate> namespace_object_template = templ.As<v8::ObjectTemplate>();
        templ->Set(Symbol::GetToStringTag(isolate), v8_name, PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    }
    else
    {
        self->to_obj = 0;
        templ->ReadOnlyPrototype();
        templ->SetClassName(v8_name);
        templ->PrototypeTemplate()->Set(Symbol::GetToStringTag(isolate), v8_name, PropertyAttribute(v8::ReadOnly | v8::DontEnum));
    }

    Py_XDECREF(class_dict);

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

    Local<Name> js_name = js_from_py(member_name, no_ctx).As<Name>();
    Local<Data> js_value = js_from_py(member_value, no_ctx);
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
    Context::Scope cs(context);
    delete_constructor(self, context);
    Local<Function> js_parent;
    if (parent != NULL) {
        if(PyObject_HasAttrString(parent->cls, "__v8_skip_inherit__"))
            parent = (py_class *)parent->parent;

        if (parent != NULL)
            js_parent = py_class_get_constructor(context, parent, (py_class*)parent->parent);
    }
    Local<Function> function = self->templ.Get(isolate)->GetFunction(context).ToLocalChecked();
    if (!js_parent.IsEmpty())
        function->SetPrototype(context, js_parent).ToChecked();
    function->SetName(js_from_py(self->cls_name, context).As<String>());
    return function;
}

void 
py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context)
{
    //PyObject_Print(py_object, stdout, Py_PRINT_RAW);
    js_object->SetInternalField(0, IZ_DAT_OBJECT);
    js_object->SetInternalField(1, External::New(isolate, py_object));
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
    if (last_proto_object->GetPrivate(context, v8::Private::ForApi(isolate, JSTR("__proto__"))).ToLocalChecked()->StrictEquals(I_CAN_HAZ_ERROR_PROTOTYPE)) {
        last_proto_object->DeletePrivate(context, v8::Private::ForApi(isolate, JSTR("__proto__"))).FromJust();
        last_proto_object->SetPrototype(context, context->GetEmbedderData(ERROR_PROTOTYPE_SLOT));
    }
    context_set_cached_jsobject(context, py_object, js_object);
}

Local<Object> 
py_class_create_js_object(py_class *self, PyObject *py_object, Local<Context> context)
{
    EscapableHandleScope hs(isolate);
    Local<Object> object = context_get_cached_jsobject(context, py_object);
    if (!object.IsEmpty()) {
        return hs.Escape(object);
    }
    object = self->templ.Get(isolate)->InstanceTemplate()->NewInstance(context).ToLocalChecked();
    Py_INCREF(py_object);

    delete_constructor(self, context);
    py_class_init_js_object(object, py_object, context);
    return hs.Escape(object);
}


void 
delete_constructor(py_class* self, Local<Context> context)
{
    if (!self->has_constructor) {
        self->has_constructor = 1;
        v8::Local<v8::FunctionTemplate> interface_template = self->templ.Get(isolate);
        v8::Local<v8::Function> function;
        if (interface_template->GetFunction(context).ToLocal(&function)) {
            v8::Local<v8::Value> prototype_value;
            if (function->Get(context, V8String(isolate, "prototype")).ToLocal(&prototype_value)) {
                v8::Local<v8::Object> prototype_object = prototype_value.As<v8::Object>();
                prototype_object->Delete(context, V8String(isolate, "constructor")).ToChecked();
            }
        }
    }

    if (self->parent != NULL) {
        delete_constructor((py_class*)self->parent, context);
    }
}