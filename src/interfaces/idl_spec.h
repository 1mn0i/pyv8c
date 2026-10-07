#ifndef IDL_SPEC_H
#define IDL_SPEC_H

#include <Python.h>
#include <cstdint>

struct IdlSpec
{
    PyObject_HEAD
    bool callbacks_resolved = true;
    int64_t length = 0;
    int64_t array = 0;
    int64_t immutable = 0;
    int64_t has_constructor = 0;
    int64_t constructor_behavior = 0;
    bool global_this = false;
    bool undetectable = false;
    bool as_object = false;
    bool unconstructable = false;
    bool hidden = false;
    bool skip_inherit = false;
    bool named_properties_object = false;
    bool code_like = false;
    PyObject *attributes = nullptr;
    PyObject *methods = nullptr;
    PyObject *constants_value = nullptr;
    PyObject *call = nullptr;
    PyObject *constructor_adapter = nullptr;
    PyObject *parent_class = nullptr;
    PyObject *to_string_tag = nullptr;
    PyObject *name = nullptr;
    PyObject *name_get = nullptr;
    PyObject *name_set = nullptr;
    PyObject *name_query = nullptr;
    PyObject *name_enum = nullptr;
    PyObject *name_del = nullptr;
    PyObject *index_get = nullptr;
    PyObject *index_set = nullptr;
    PyObject *index_query = nullptr;
    PyObject *index_enum = nullptr;
    PyObject *index_del = nullptr;
    PyObject *iterate = nullptr;
    PyObject *cross_origin_check = nullptr;
    PyObject *cross_origin_name_get = nullptr;
    PyObject *cross_origin_name_set = nullptr;
    PyObject *cross_origin_name_query = nullptr;
    PyObject *cross_origin_name_enum = nullptr;
    PyObject *cross_origin_name_del = nullptr;
    PyObject *cross_origin_index_get = nullptr;
    PyObject *cross_origin_index_set = nullptr;
    PyObject *cross_origin_index_query = nullptr;
    PyObject *cross_origin_index_enum = nullptr;
    PyObject *cross_origin_index_del = nullptr;
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
