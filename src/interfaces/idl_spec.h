#ifndef IDL_SPEC_H
#define IDL_SPEC_H

#include <Python.h>
#include <cstdint>

//: Which interface object an IDL definition builds.  ``pyclass.cpp`` branches on it, a document
//: states its number, and ``pyv8.DefinitionKind`` names it; the names are Blink's --
//: ``WrapperTypeInfo::IdlDefinitionKind`` (``kInterface``, ``kIdlNamespace``) with
//: ``kWrapperTypeNoPrototype``, the field Blink gives a callback interface.
enum IdlDefinitionKind
{
    kInterface = 0,               // a function with a prototype
    kIdlNamespace = 1,            // a plain object
    kWrapperTypeNoPrototype = 2,  // a function with no prototype at all
};

enum IdlIterationKind : int64_t
{
    kIterationNone = 0,
    kIterationIndexed = 1,
    kIterationPair = 2,
    kIterationSet = 3,
};

struct IdlSpec
{
    PyObject_HEAD
    bool callbacks_resolved = true;
    int64_t length = 0;
    int64_t array_methods = 0;
    int64_t iteration_kind = kIterationNone;
    int64_t iteration_methods = 31;
    int64_t immutable = 0;
    int64_t has_constructor = 0;
    int64_t constructor_behavior = 0;
    bool global_this = false;
    bool undetectable = false;
    //: One of ``IdlDefinitionKind``; the field takes those words and reads back as the one it was
    //: set with.  A namespace and a callback interface are exclusive by construction, which two
    //: flags could not say: a namespace has no prototype either.
    int64_t definition_kind = kInterface;
    bool unconstructable = false;
    bool hidden = false;
    bool skip_inherit = false;
    bool named_properties_object = false;
    bool named_properties_non_masking = false;
    bool code_like = false;
    PyObject *attributes = nullptr;
    PyObject *methods = nullptr;
    PyObject *constants_value = nullptr;
    PyObject *prototype_late_members = nullptr;
    PyObject *call = nullptr;
    PyObject *constructor_adapter = nullptr;
    PyObject *parent_class = nullptr;
    PyObject *to_string_tag = nullptr;
    PyObject *name = nullptr;
    // Handler storage and constructor metadata share the same declaration table.
#define PYV8_HANDLER(id, declaration, field) PyObject* field = nullptr;
#define PYV8_PROPERTY_NAME_HANDLER(id, declaration)
#include "handler_table.def"
#undef PYV8_PROPERTY_NAME_HANDLER
#undef PYV8_HANDLER
};
extern PyTypeObject *idl_spec_type;

struct AttributeSpec
{
    PyObject_HEAD
    bool callbacks_resolved = true; bool getter_receiver = true;
    bool setter_receiver = true;
    PyObject *name = nullptr;
    PyObject *getter = nullptr;
    PyObject *setter = nullptr;
    int64_t location = 1;
    int64_t property_flags = 0;
    int64_t receiver_check = 0;
    int64_t cross_origin_get_check = 0;
    int64_t cross_origin_set_check = 0;
    int64_t side_effect = 0;
    bool replaceable = false;
};
extern PyTypeObject *attribute_spec_type;

struct MethodSpec
{
    PyObject_HEAD
    bool callbacks_resolved = true; bool pass_receiver = true;
    PyObject *name = nullptr;
    PyObject *callback = nullptr;
    PyObject *trace_receiver = nullptr;
    int64_t length = 0;
    int64_t location = 1;
    int64_t property_flags = 0;
    int64_t receiver_check = 0;
    int64_t cross_origin_check = 0;
    int64_t side_effect = 0;
};
extern PyTypeObject *method_spec_type;

struct ConstantSpec
{
    PyObject_HEAD PyObject *name = nullptr;
    int64_t value = 0;
};
extern PyTypeObject *constant_spec_type;

int idl_spec_init(PyObject *module);
PyObject *idl_spec_for(PyObject *owner);
PyObject *idl_spec_replace(PyObject *original, PyObject *changes);
PyObject *idl_spec_resolve(PyObject *original, PyObject *owner);
bool idl_spec_flag(PyObject *owner, const char *key, bool *result);

#endif
