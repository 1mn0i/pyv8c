#include <Python.h>
#include "pyv8env.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "pyfunction.h"
#include "pyclass.h"
#include "v8tools.h"
#include "faker/vm.h"


//static PyObject* __dict__ = PyString_InternFromString("__dict__");

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

PyTypeObject py_class_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};

int py_class_type_init() {
    py_class_type.tp_name = "pyv8env.Class";
    py_class_type.tp_basicsize = sizeof(py_class);
    py_class_type.tp_flags = Py_TPFLAGS_DEFAULT;
    py_class_type.tp_doc = "v8 Class";
    py_class_type.tp_dealloc = (destructor)py_class_dealloc;
    return PyType_Ready(&py_class_type);
}

void py_class_dealloc(py_class* self) {
    Py_XDECREF(self->cls);
    Py_XDECREF(self->cls_name);
    //Py_XDECREF(self->parent);
    self->templ.Reset();
    Py_TYPE(self)->tp_free((PyObject*)self);
}

PyObject *py_class_to_template(PyObject *cls, context_c* py_ctx) {
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

int add_class_to_template(PyObject* cls, Local<FunctionTemplate> templ, Local<Signature> signature, const char* world, context_c* py_ctx);
int add_to_template(PyObject* cls, context_c* py_ctx, PyObject* member_name, PyObject* member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world);


PyObject *py_class_new(PyObject *cls, context_c * py_ctx) {
    IN_V8;

    // The convert functions require a context for function conversion, but we
    // don't have a context. So we use an empty context and special-case
    // functions.
    Local<Context> no_ctx;

    assert(cls != (PyObject *) &PyBaseObject_Type);
    PyObject *bases; // borrowed reference
        bases = ((PyTypeObject *) cls)->tp_bases;

    py_class *self = (py_class *) py_class_type.tp_alloc(&py_class_type, 0);
    PyErr_PROPAGATE(self);

    Py_INCREF(cls);
    self->cls = cls;
    self->cls_name = PyObject_GetAttrString(cls, "__name__");
    if (self->cls_name == NULL) {
        Py_XDECREF(self->cls);
        return NULL;
    }

    PyObject* class_dict = ((PyTypeObject*)cls)->tp_dict;

    Local<External> js_self = External::New(isolate, self);
    int templ_lenght = 0;
    PyObject* lenght = PyDict_GetItemString(class_dict, "__v8_length__");
    if (lenght != NULL && PyLong_Check(lenght)) {
        templ_lenght = PyLong_AsLong(lenght);
    }

    Local<FunctionTemplate> templ = FunctionTemplate::New(isolate, py_class_construct_callback, js_self,
            Local<Signature>(), templ_lenght, ConstructorBehavior::kAllow);
    
    const char* world = PyUnicode_AsUTF8(self->cls_name);
    Local<String> v8_name = V8String(isolate, world);


    if (PyDict_GetItemString(class_dict, "__v8_as_obj__") != NULL) {
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

    Local<Signature> sig = Signature::New(isolate, templ);
    if (PyDict_GetItemString(class_dict, "__v8_global_this__") != NULL)
        faker::Register(isolate, templ);

    //add_class_to_template(cls, templ, sig, world, py_ctx);
    PyObject* member_name, * member_value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(class_dict, &pos, &member_name, &member_value)) {
        add_to_template(cls, py_ctx, member_name, member_value, templ, sig, world);
    }

    if (PyDict_GetItemString(class_dict, "__v8_attribute__") != NULL)
        bindings::InstallAttributes(isolate, world, templ, sig, cls, PyDict_GetItemString(class_dict, "__v8_attribute__"));
    if (PyDict_GetItemString(class_dict, "__v8_method__") != NULL)
        bindings::InstallOperations(isolate, world, templ, sig, cls, PyDict_GetItemString(class_dict, "__v8_method__"));
    if (PyDict_GetItemString(class_dict, "__v8_constants_value__"))
        bindings::InstallConstantValues(isolate, world, templ, sig, PyDict_GetItemString(class_dict, "__v8_constants_value__"));
    if (PyDict_GetItemString(class_dict, "__v8_constants_callback__") != NULL)
        bindings::InstallConstantCallbacks(isolate, world, templ, sig, PyDict_GetItemString(class_dict, "__v8_constants_callback__"));
    if (PyDict_GetItemString(class_dict, "__call__") != NULL)
        templ->InstanceTemplate()->SetCallAsFunctionHandler(py_class_method_callback, External::New(isolate, PyDict_GetItemString(class_dict, "__call__")));
    if (PyDict_GetItemString(class_dict, "__v8_undetectable__") != NULL)
        templ->InstanceTemplate()->MarkAsUndetectable();
    if (PyDict_GetItemString(class_dict, "__v8_array__") != NULL) {
        int flag = PyLong_AsLong(PyDict_GetItemString(class_dict, "__v8_array__"));
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
    if (PyDict_GetItemString(class_dict, "__v8_immutable__")) {
        int flag = PyLong_AsLong(PyDict_GetItemString(class_dict, "__v8_immutable__"));
        if ((flag & FlagImmutable::instance) != 0)
            templ->InstanceTemplate()->SetImmutableProto();
        if ((flag & FlagImmutable::prototype) != 0)
            templ->PrototypeTemplate()->SetImmutableProto();
    }
    if (PyDict_GetItemString(class_dict, "__v8_has_constructor__")) {
        self->constructor = PyLong_AsLong(PyDict_GetItemString(class_dict, "__v8_has_constructor__"));
    }

    if ((py_ctx->hook || PyDict_GetItemString(class_dict, "__v8_name_get__") != NULL) && strcmp(world, "WindowProperties") != 0) {
        NamedPropertyHandlerConfiguration callbacks(named_getter);
        if (py_ctx->hook || PyDict_GetItemString(class_dict, "__v8_name_set__") != NULL) {
            callbacks.setter = named_setter;
        }
        if (PyDict_GetItemString(class_dict, "__v8_name_query__") != NULL) {
            callbacks.query = named_query;
        }
        if (PyDict_GetItemString(class_dict, "__v8_name_enum__") != NULL) {
            callbacks.enumerator = named_enumerator;
        }
        if (PyDict_GetItemString(class_dict, "__v8_name_del__") != NULL) {
            callbacks.deleter = named_deleter;
        }
        templ->InstanceTemplate()->SetHandler(callbacks);

    }
    // if __getitem__ and __len__ are defined, it's a sequence.
    if ((py_ctx->hook || PyDict_GetItemString(class_dict, "__v8_index_get__") != NULL) && strcmp(world, "WindowProperties") != 0) {
        IndexedPropertyHandlerConfiguration callbacks;
        callbacks.getter = indexed_getter;
        if (py_ctx->hook || PyDict_GetItemString(class_dict, "__v8_index_set__") != NULL) {
            callbacks.setter = indexed_setter;
        }
        if (PyDict_GetItemString(class_dict, "__v8_index_enum__") != NULL) {
            callbacks.enumerator = indexed_enumerator;
        }
        if (PyDict_GetItemString(class_dict, "__v8_index_query__") != NULL) {
            callbacks.query = indexed_query;
        }
        if (PyDict_GetItemString(class_dict, "__v8_index_del__") != NULL) {
            callbacks.deleter = indexed_deleter;
        }
        templ->InstanceTemplate()->SetHandler(callbacks);
    }

    if (PyDict_GetItemString(class_dict, "__v8__cross_origin_check__") != NULL) {
        templ->InstanceTemplate()->SetAccessCheckCallback(CrossOriginAccessCheckCallback);
        //printf("pyclass 1\n");
        //fflush(stdout);
        //templ->InstanceTemplate()->SetAccessCheckCallbackAndHandler(CrossOriginAccessCheckCallback, name_handler, index_handler);
    }

    if (strcmp(world, "WindowProperties") == 0) {
        NamedPropertyHandlerConfiguration name_callbacks(window_properties_named_getter);
        templ->PrototypeTemplate()->SetHandler(name_callbacks);
    }

    PyObject* last_base = PyTuple_GET_ITEM(bases, PyTuple_GET_SIZE(bases) - 1);
    if (last_base == NULL) {
        PyErr_Clear();
    }
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
    templ->InstanceTemplate()->SetInternalFieldCount(OBJECT_INTERNAL_FIELDS);

    self->templ.Reset(isolate, templ);

    return (PyObject *) self;
}
                                                       


int add_class_to_template(PyObject *cls, Local<FunctionTemplate> templ, Local<Signature> signature, const char* world, context_c* py_ctx) {
    PyObject *dict;
    if (PyClass_Check(cls)) {
        // old style
        dict = PyObject_GetAttr(cls, PyString_InternFromString("__dict__"));
    } else {
        // new style
        // Sorry, but I have to do this to use PyDict_Next. At least the layout
        // is the same on 2 and 3.
        struct dictproxy {
            PyObject_HEAD
            PyObject *dict;
        };
        struct dictproxy *proxy = (struct dictproxy *) PyObject_GenericGetAttr(cls, PyString_InternFromString("__dict__"));
        dict = proxy->dict;
        Py_INCREF(dict);
        Py_XDECREF(proxy);
    }
    PyErr_PROPAGATE(dict);
    PyObject *member_name, *member_value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(dict, &pos, &member_name, &member_value)) {
        Py_INCREF(member_name);
        Py_INCREF(member_value);
        if (add_to_template(cls, py_ctx, member_name, member_value, templ, signature, world) < 0) {
            Py_XDECREF(dict);
            return -1;
        }
    }
    Py_XDECREF(dict);
    return 0;
}

bool if_property_has(PyObject *property, const char *what) {
    if (!PyObject_TypeCheck(property, &PyProperty_Type))
        return true;
    PyObject *value = PyObject_GetAttrString(property, what);
    Py_XDECREF(value);
    return value == Py_None;
}

// 0 on success, -1 on failure
int add_to_template(PyObject *cls, context_c* py_ctx, PyObject *member_name, PyObject *member_value, Local<FunctionTemplate> templ, Local<Signature> sig, const char* world) {
    HandleScope hs(isolate);
    Local<Context> no_ctx;
    // I can't use an AccessorSignature here because then property access on
    // the global object from this gets fucked up.
    if (PyString_Check(member_name) && PyUnicode_AsUTF8(member_name)[0] == '_') {
        return 0;
    }

    if (PyObject_HasAttrString(member_value, "__v8_hidden__")) {
        return 0;
    }

    Local<Name> js_name = js_from_py(member_name, no_ctx).As<Name>();
    Local<Data> js_value;

    if (PyFunction_Check(member_value)) {
        //if (!PyObject_HasAttrString(member_value, "__v8_method"))
        //    return 0;

        //// if it's an unbound method, create a method
        //// the member value is supposed to be not increfed because the class should hold a reference to it
        //bindings::OperationConfig config;
        //config.name = PyUnicode_AsUTF8(member_name);
        //config.callback = py_class_method_callback;
        //config.js_method = External::New(isolate, member_value);

        //Set_Config_Value(member_value, "__v8_length", config, length);
        //Set_Config_Value(member_value, "__v8_property_attribute", config, v8_property_attribute);
        //Set_Config_Value(member_value, "__v8_location", config, location);
        //Set_Config_Value(member_value, "__v8_receiver_check", config, receiver_check);
        //Set_Config_Value(member_value, "__v8_cross_origin_check", config, cross_origin_check);
        //Set_Config_Value(member_value, "__v8_side_effect", config, v8_side_effect);

        //bindings::InstallOperation(isolate, world, templ, sig, config);
        return 0;
    }
    
    if (PyObject_HasAttrString(member_value, "__get__") && !PyFunction_Check(member_value)) {
        // if it's a descriptor, make an accessor
 /*       bindings::AttributeConfig config;
        PyObject * fget_func = PyObject_GetAttrString(member_value, "fget");
        if (fget_func == NULL) {
            return -1;
        }
        Set_Config_Value(fget_func, "__v8_location", config, location);
        Set_Config_Value(fget_func, "__v8_property_attribute", config, v8_property_attribute);
        Set_Config_Value(fget_func, "__v8_receiver_check", config, receiver_check);
        Set_Config_Value(fget_func, "__v8_cross_origin_check_for_get", config, cross_origin_check_for_get);
        Set_Config_Value(fget_func, "__v8_cross_origin_check_for_set", config, cross_origin_check_for_set);
        Set_Config_Value(fget_func, "__v8_side_effect", config, v8_side_effect);
        Py_XDECREF(fget_func);

        config.name = PyUnicode_AsUTF8(member_name);
        config.callback_for_get_name = V8String(isolate, config.name);
        config.callback_for_get = py_attribute_for_get;

        config.callback_for_set_name = V8String(isolate, config.name);
        if (config.location == unsigned(bindings::FlagLocation::kInstance)) {
            config.callback_for_set = py_attribute_for_instance_set;
        }
        else {
            config.callback_for_set = py_attribute_for_property_set;
        }
        if (!PyObject_HasAttrString(member_value, "__set__") || if_property_has(member_value, "fset")) {
            config.callback_for_set = nullptr;
        }
        bindings::InstallAttribute(isolate, world, templ, sig, config);*/
        return 0;
    }

    if (PyLong_Check(member_value)) {
        bindings::ConstantValueConfig config;
        config.name = PyUnicode_AsUTF8(member_name);
        config.value = PyLong_AsLong(member_value);
        bindings::InstallConstantValue(isolate, world, templ, sig, config);
        return 0;
    }

    // otherwise just convert
    if (PyType_Check(member_value) && std::isupper(static_cast<unsigned char>(PyUnicode_AsUTF8(member_name)[0]))) {
        py_class_to_template(member_value, py_ctx);
        return 0;
    }
    js_value = js_from_py(member_value, no_ctx);
    if (!js_value.IsEmpty()) {
        templ->Set(js_name, js_value);
        templ->PrototypeTemplate()->Set(js_name, js_value);
        return 0;
    }
    return -1;
}

Local<Function> py_class_get_constructor(py_class *self, Local<Context> context) {
    EscapableHandleScope hs(isolate);
    Local<Function> function = self->templ.Get(isolate)->GetFunction(context).ToLocalChecked();
    delete_constructor(self, context);
    function->SetName(js_from_py(self->cls_name, context).As<String>());
    return hs.Escape(function);
}

void py_class_init_js_object(Local<Object> js_object, PyObject *py_object, Local<Context> context) {
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

Local<Object> py_class_create_js_object(py_class *self, PyObject *py_object, Local<Context> context) {
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


void delete_constructor(py_class* self, Local<Context> context) {
    if (!self->constructor) {
        self->constructor = 1;
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