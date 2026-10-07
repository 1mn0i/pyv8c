#include "pyv8.h"
#include "idl_spec.h"
#include <cstddef>
#include <cstring>

PyTypeObject *idl_spec_type = nullptr;
PyTypeObject *attribute_spec_type = nullptr;
PyTypeObject *method_spec_type = nullptr;
PyTypeObject *constant_spec_type = nullptr;

enum class Kind
{
    Int,
    Signed,
    Bool,
    String,
    RequiredString,
    Callback,
    RequiredCallback,
    Type,
    Attributes,
    Methods,
    Constants
};
struct Field
{
    const char *name;
    Kind kind;
    size_t offset;
    int64_t initial;
    int64_t maximum;
};
//: ``array`` and ``definition_kind`` are plain ints here: ``pyv8.ArrayProto`` and
//: ``pyv8.DefinitionKind`` name the numbers a caller means, and the binding installs from them.
static Field IdlSpec_fields[] = {
    {"length", Kind::Int, offsetof(IdlSpec, length), 0, 2147483647},
    {"array", Kind::Int, offsetof(IdlSpec, array), 0, 31},
    {"immutable", Kind::Int, offsetof(IdlSpec, immutable), 0, 3},
    {"has_constructor", Kind::Int, offsetof(IdlSpec, has_constructor), 0, 1},
    {"constructor_behavior", Kind::Int, offsetof(IdlSpec, constructor_behavior), 0, 3},
    {"global_this", Kind::Bool, offsetof(IdlSpec, global_this), 0, 1},
    {"undetectable", Kind::Bool, offsetof(IdlSpec, undetectable), 0, 1},
    {"definition_kind", Kind::Int, offsetof(IdlSpec, definition_kind), 0, 2},
    {"unconstructable", Kind::Bool, offsetof(IdlSpec, unconstructable), 0, 1},
    {"hidden", Kind::Bool, offsetof(IdlSpec, hidden), 0, 1},
    {"skip_inherit", Kind::Bool, offsetof(IdlSpec, skip_inherit), 0, 1},
    {"named_properties_object", Kind::Bool, offsetof(IdlSpec, named_properties_object), 0, 1},
    {"code_like", Kind::Bool, offsetof(IdlSpec, code_like), 0, 1},
    {"attributes", Kind::Attributes, offsetof(IdlSpec, attributes), 0, 0},
    {"methods", Kind::Methods, offsetof(IdlSpec, methods), 0, 0},
    {"constants_value", Kind::Constants, offsetof(IdlSpec, constants_value), 0, 0},
    {"call", Kind::Callback, offsetof(IdlSpec, call), 0, 0},
    {"constructor_adapter", Kind::Callback, offsetof(IdlSpec, constructor_adapter), 0, 0},
    {"parent_class", Kind::Type, offsetof(IdlSpec, parent_class), 0, 0},
    {"to_string_tag", Kind::String, offsetof(IdlSpec, to_string_tag), 0, 0},
    {"name", Kind::String, offsetof(IdlSpec, name), 0, 0},
    {"name_get", Kind::Callback, offsetof(IdlSpec, name_get), 0, 0},
    {"name_set", Kind::Callback, offsetof(IdlSpec, name_set), 0, 0},
    {"name_query", Kind::Callback, offsetof(IdlSpec, name_query), 0, 0},
    {"name_enum", Kind::Callback, offsetof(IdlSpec, name_enum), 0, 0},
    {"name_del", Kind::Callback, offsetof(IdlSpec, name_del), 0, 0},
    {"index_get", Kind::Callback, offsetof(IdlSpec, index_get), 0, 0},
    {"index_set", Kind::Callback, offsetof(IdlSpec, index_set), 0, 0},
    {"index_query", Kind::Callback, offsetof(IdlSpec, index_query), 0, 0},
    {"index_enum", Kind::Callback, offsetof(IdlSpec, index_enum), 0, 0},
    {"index_del", Kind::Callback, offsetof(IdlSpec, index_del), 0, 0},
    {"iterate", Kind::Callback, offsetof(IdlSpec, iterate), 0, 0},
    {"cross_origin_check", Kind::Callback, offsetof(IdlSpec, cross_origin_check), 0, 0},
    {"cross_origin_name_get", Kind::Callback, offsetof(IdlSpec, cross_origin_name_get), 0, 0},
    {"cross_origin_name_set", Kind::Callback, offsetof(IdlSpec, cross_origin_name_set), 0, 0},
    {"cross_origin_name_query", Kind::Callback, offsetof(IdlSpec, cross_origin_name_query), 0, 0},
    {"cross_origin_name_enum", Kind::Callback, offsetof(IdlSpec, cross_origin_name_enum), 0, 0},
    {"cross_origin_name_del", Kind::Callback, offsetof(IdlSpec, cross_origin_name_del), 0, 0},
    {"cross_origin_index_get", Kind::Callback, offsetof(IdlSpec, cross_origin_index_get), 0, 0},
    {"cross_origin_index_set", Kind::Callback, offsetof(IdlSpec, cross_origin_index_set), 0, 0},
    {"cross_origin_index_query", Kind::Callback, offsetof(IdlSpec, cross_origin_index_query), 0, 0},
    {"cross_origin_index_enum", Kind::Callback, offsetof(IdlSpec, cross_origin_index_enum), 0, 0},
    {"cross_origin_index_del", Kind::Callback, offsetof(IdlSpec, cross_origin_index_del), 0, 0},
};
static Field AttributeSpec_fields[] = {
    {"name", Kind::RequiredString, offsetof(AttributeSpec, name), 0, 0},
    {"getter", Kind::RequiredCallback, offsetof(AttributeSpec, getter), 0, 0},
    {"setter", Kind::Callback, offsetof(AttributeSpec, setter), 0, 0},
    {"location", Kind::Int, offsetof(AttributeSpec, location), 1, 2},
    {"property_flags", Kind::Int, offsetof(AttributeSpec, property_flags), 0, 7},
    {"receiver_check", Kind::Int, offsetof(AttributeSpec, receiver_check), 0, 1},
    {"cross_origin_get_check", Kind::Int, offsetof(AttributeSpec, cross_origin_get_check), 0, 1},
    {"cross_origin_set_check", Kind::Int, offsetof(AttributeSpec, cross_origin_set_check), 0, 1},
    {"side_effect", Kind::Int, offsetof(AttributeSpec, side_effect), 0, 2},
    {"replaceable", Kind::Bool, offsetof(AttributeSpec, replaceable), 0, 1},
};
static Field MethodSpec_fields[] = {
    {"name", Kind::RequiredString, offsetof(MethodSpec, name), 0, 0},
    {"callback", Kind::RequiredCallback, offsetof(MethodSpec, callback), 0, 0},
    {"length", Kind::Int, offsetof(MethodSpec, length), 0, 255},
    {"location", Kind::Int, offsetof(MethodSpec, location), 1, 2},
    {"property_flags", Kind::Int, offsetof(MethodSpec, property_flags), 0, 7},
    {"receiver_check", Kind::Int, offsetof(MethodSpec, receiver_check), 0, 1},
    {"cross_origin_check", Kind::Int, offsetof(MethodSpec, cross_origin_check), 0, 1},
    {"side_effect", Kind::Int, offsetof(MethodSpec, side_effect), 0, 2},
};
static Field ConstantSpec_fields[] = {
    {"name", Kind::RequiredString, offsetof(ConstantSpec, name), 0, 0},
    {"value", Kind::Signed, offsetof(ConstantSpec, value), 0, 0},
};
struct Definition
{
    PyTypeObject **type;
    const char *name;
    size_t size;
    Field *fields;
    size_t count;
    PyGetSetDef *getters;
};
// The count is the array's own length: a field added above is registered without
// anyone having to remember to bump a number, which is what kept the last field of
// IdlSpec unregistered.
#define PYV8_FIELD_COUNT(fields) (sizeof(fields) / sizeof((fields)[0]))
static Definition definitions[] = {
    {&idl_spec_type, "pyv8.IdlSpec", sizeof(IdlSpec), IdlSpec_fields, PYV8_FIELD_COUNT(IdlSpec_fields), nullptr},
    {&attribute_spec_type, "pyv8.AttributeSpec", sizeof(AttributeSpec), AttributeSpec_fields, PYV8_FIELD_COUNT(AttributeSpec_fields), nullptr},
    {&method_spec_type, "pyv8.MethodSpec", sizeof(MethodSpec), MethodSpec_fields, PYV8_FIELD_COUNT(MethodSpec_fields), nullptr},
    {&constant_spec_type, "pyv8.ConstantSpec", sizeof(ConstantSpec), ConstantSpec_fields, PYV8_FIELD_COUNT(ConstantSpec_fields), nullptr},
};
#undef PYV8_FIELD_COUNT
static Definition &definition(PyTypeObject *type)
{
    for (auto &item : definitions)
        if (*item.type == type)
            return item;
    return definitions[0];
}
static bool object_field(const Field &field)
{
    return field.kind != Kind::Int && field.kind != Kind::Signed && field.kind != Kind::Bool;
}
static void *address(PyObject *self, const Field &field)
{
    return (char *)self + field.offset;
}
static PyObject *field_get(PyObject *self, void *closure)
{
    const Field &field = *(Field *)closure;
    if (field.kind == Kind::Bool)
        return PyBool_FromLong(*(bool *)address(self, field));
    if (!object_field(field))
        return PyLong_FromLongLong(*(int64_t *)address(self, field));
    PyObject *value = *(PyObject **)address(self, field);
    if (value == nullptr)
    {
        if (field.kind >= Kind::Attributes)
            return PyTuple_New(0);
        value = Py_None;
    }
    Py_INCREF((PyObject*)value);
    return value;
}
static int assign_field(PyObject *self, const Field &field, PyObject *value)
{
    if (field.kind == Kind::Bool)
    {
        if (!PyBool_Check(value))
            goto invalid;
        *(bool *)address(self, field) = value == Py_True;
        return 0;
    }
    if (!object_field(field))
    {
        if (field.kind == Kind::Int)
        {
            //: An int field states its own range: ``array`` and ``definition_kind`` are numbers a
            //: caller spells through ``pyv8.ArrayProto`` and ``pyv8.DefinitionKind`` rather than
            //: guesses, and the rest are limits worth naming when one is hit.
            bool integral = PyLong_Check(value) != 0;
            int64_t number = integral ? PyLong_AsLongLong(value) : -1;
            if (PyErr_Occurred())
                return -1;
            if (!integral || number < 0 || number > field.maximum)
            {
                PyErr_Format(PyExc_TypeError, "%s.%s takes an integer in 0..%lld, not %R",
                             definition(Py_TYPE((PyObject*)self)).name, field.name,
                             (long long)field.maximum, value);
                return -1;
            }
            *(int64_t *)address(self, field) = number;
            return 0;
        }
        if (!PyLong_Check(value))
            goto invalid;
        int64_t number = PyLong_AsLongLong(value);
        if (PyErr_Occurred())
            return -1;
        if (field.kind == Kind::Signed && (number < INT32_MIN || number > UINT32_MAX))
        {
            PyErr_SetString(PyExc_OverflowError, "constant is outside the V8 Integer range");
            return -1;
        }
        *(int64_t *)address(self, field) = number;
        return 0;
    }
    switch (field.kind)
    {
    case Kind::String:
        if (value == Py_None)
            break;
        [[fallthrough]];
    case Kind::RequiredString:
        if (!PyUnicode_Check(value) || PyUnicode_FindChar(value, 0, 0, PyUnicode_GetLength(value), 1) >= 0)
            goto invalid;
        break;
    case Kind::Callback:
        if (value == Py_None)
            break;
        [[fallthrough]];
    case Kind::RequiredCallback:
        if (!PyUnicode_Check(value) && !PyCallable_Check(value))
            goto invalid;
        if (PyUnicode_Check(value) && PyUnicode_FindChar(value, 0, 0, PyUnicode_GetLength(value), 1) >= 0)
            goto invalid;
        break;
    case Kind::Type:
        if (value != Py_None && !PyType_Check(value))
            goto invalid;
        break;
    case Kind::Attributes:
    case Kind::Methods:
    case Kind::Constants: {
        if (!PyTuple_Check(value))
            goto invalid;
        PyTypeObject *expected = field.kind == Kind::Attributes  ? attribute_spec_type
                                 : field.kind == Kind::Methods   ? method_spec_type
                                                                 : constant_spec_type;
        for (Py_ssize_t index = 0; index < PyTuple_Size(value); ++index)
            if (Py_TYPE(PyTuple_GetItem(value, index)) != expected)
                goto invalid;
        break;
    }
    default:
        break;
    }
    if (value == Py_None)
        value = nullptr;
    Py_XINCREF((PyObject*)value);
    Py_XDECREF(*(PyObject **)address(self, field));
    *(PyObject **)address(self, field) = value;
    return 0;
invalid:
    PyErr_Format(PyExc_TypeError, "%s.%s has an invalid value or type", definition(Py_TYPE((PyObject*)self)).name,
                 field.name);
    return -1;
}
static int apply_fields(PyObject *self, PyObject *changes)
{
    auto &def = definition(Py_TYPE((PyObject*)self));
    Py_ssize_t position = 0;
    PyObject *key = nullptr;
    PyObject *value = nullptr;
    while (changes != nullptr && PyDict_Next(changes, &position, &key, &value))
    {
        Field *found = nullptr;
        if (PyUnicode_Check(key))
        {
            for (size_t index = 0; index < def.count; ++index)
                if (PyUnicode_CompareWithASCIIString(key, def.fields[index].name) == 0)
                {
                    found = &def.fields[index];
                    break;
                }
        }
        if (found == nullptr)
        {
            PyErr_Format(PyExc_TypeError, "unknown %s field: %R", def.name, key);
            return -1;
        }
        if (assign_field(self, *found, value) < 0)
            return -1;
    }
    bool callbacks_resolved = true;
    for (size_t index = 0; index < def.count; ++index)
    {
        const Field &field = def.fields[index];
        if (field.kind == Kind::Callback || field.kind == Kind::RequiredCallback)
        {
            PyObject *reference = *(PyObject **)address(self, field);
            if (reference != nullptr && PyUnicode_Check(reference))
                callbacks_resolved = false;
        }
        if ((field.kind == Kind::RequiredString || field.kind == Kind::RequiredCallback) &&
            *(PyObject **)address(self, field) == nullptr)
        {
            PyErr_Format(PyExc_TypeError, "%s.%s is required", def.name, field.name);
            return -1;
        }
    }
    if (Py_TYPE((PyObject*)self) == method_spec_type)
    {
        auto &spec = *(MethodSpec *)self;
        spec.callbacks_resolved = callbacks_resolved;
        spec.pass_receiver = spec.location != 2 && !PyMethod_Check_abi3(spec.callback);
        Py_CLEAR(spec.trace_receiver);
        if (PyMethod_Check_abi3(spec.callback)) {
            spec.trace_receiver = PyObject_GetAttrString(spec.callback, "__self__");
            if (!spec.trace_receiver) return -1;
        }
    }
    if (Py_TYPE((PyObject*)self) == attribute_spec_type)
    {
        auto &spec = *(AttributeSpec *)self;
        spec.callbacks_resolved = callbacks_resolved;
        spec.getter_receiver = spec.location != 2 && !PyMethod_Check_abi3(spec.getter);
        spec.setter_receiver =
            spec.location != 2 && (spec.setter == nullptr || !PyMethod_Check_abi3(spec.setter));
    }
    if (Py_TYPE((PyObject*)self) == idl_spec_type)
    {
        auto &spec = *(IdlSpec *)self;
        spec.callbacks_resolved = callbacks_resolved;
        if (spec.undetectable && spec.call == nullptr)
        {
            PyErr_SetString(PyExc_TypeError, "IdlSpec.undetectable requires a call callback");
            return -1;
        }
    }
    return 0;
}
static PyObject *record_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    if (PyTuple_Size(args) != 0)
    {
        PyErr_SetString(PyExc_TypeError, "IDL descriptors accept keyword fields only");
        return nullptr;
    }
    PyObject *self = PyType_GenericAlloc(type, 0);
    if (self == nullptr)
        return nullptr;
    auto &def = definition(type);
    for (size_t index = 0; index < def.count; ++index)
    {
        const Field &field = def.fields[index];
        if (!object_field(field) && field.kind != Kind::Bool)
            *(int64_t *)address(self, field) = field.initial;
    }
    if (apply_fields(self, kwargs) < 0)
    {
        Py_DECREF((PyObject*)self);
        return nullptr;
    }
    return self;
}
PyObject *idl_spec_replace(PyObject *original, PyObject *changes)
{
    PyObject *self = PyType_GenericAlloc(Py_TYPE((PyObject*)original), 0);
    if (self == nullptr)
        return nullptr;
    auto &def = definition(Py_TYPE((PyObject*)original));
    for (size_t index = 0; index < def.count; ++index)
    {
        const Field &field = def.fields[index];
        if (object_field(field))
        {
            PyObject *value = *(PyObject **)address(original, field);
            Py_XINCREF((PyObject*)value);
            *(PyObject **)address(self, field) = value;
        }
        else if (field.kind == Kind::Bool)
            *(bool *)address(self, field) = *(bool *)address(original, field);
        else
            *(int64_t *)address(self, field) = *(int64_t *)address(original, field);
    }
    if (apply_fields(self, changes) < 0)
    {
        Py_DECREF((PyObject*)self);
        return nullptr;
    }
    return self;
}
static PyObject *record_replace(PyObject *self, PyObject *args, PyObject *kwargs)
{
    if (PyTuple_Size(args) != 0)
    {
        PyErr_SetString(PyExc_TypeError, "replace accepts keyword fields only");
        return nullptr;
    }
    return idl_spec_replace(self, kwargs);
}
static int record_traverse(PyObject *self, visitproc visit, void *arg)
{
    if (Py_TYPE(self) == method_spec_type) Py_VISIT(((MethodSpec*)self)->trace_receiver);
    auto &def = definition(Py_TYPE((PyObject*)self));
    for (size_t index = 0; index < def.count; ++index)
        if (object_field(def.fields[index]))
            Py_VISIT(*(PyObject **)address(self, def.fields[index]));
    return 0;
}
static int record_clear(PyObject *self)
{
    if (Py_TYPE(self) == method_spec_type) Py_CLEAR(((MethodSpec*)self)->trace_receiver);
    auto &def = definition(Py_TYPE((PyObject*)self));
    for (size_t index = 0; index < def.count; ++index)
        if (object_field(def.fields[index]))
            Py_CLEAR(*(PyObject **)address(self, def.fields[index]));
    return 0;
}
static void record_dealloc(PyObject *self)
{
    PyObject_GC_UnTrack(self);
    record_clear(self);
    PyTypeObject *type = Py_TYPE((PyObject*)self);
    ((freefunc)PyType_GetSlot(type, Py_tp_free))(self);
    Py_DECREF((PyObject*)type);
}
static PyMethodDef methods[] = {
    {"replace", (PyCFunction)PythonBoundary<record_replace>::call, METH_VARARGS | METH_KEYWORDS, nullptr},
    {nullptr, nullptr, 0, nullptr}};
int idl_spec_init(PyObject *module)
{
    for (auto &def : definitions)
    {
        def.getters = new PyGetSetDef[def.count + 1]();
        for (size_t index = 0; index < def.count; ++index)
            def.getters[index] = {const_cast<char *>(def.fields[index].name), field_get, nullptr, nullptr,
                                  &def.fields[index]};
        PyType_Slot slots[] = {{Py_tp_new, (void *)PythonBoundary<record_new>::call},
                               {Py_tp_dealloc, (void *)PythonBoundary<record_dealloc>::call},
                               {Py_tp_traverse, (void *)PythonBoundary<record_traverse>::call},
                               {Py_tp_clear, (void *)PythonBoundary<record_clear>::call},
                               {Py_tp_methods, methods},
                               {Py_tp_getset, def.getters},
                               {0, nullptr}};
        PyType_Spec spec = {def.name, (int)def.size, 0, Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC, slots};
        *def.type = (PyTypeObject *)PyType_FromSpec(&spec);
        if (*def.type == nullptr || PyModule_AddObject(module, def.name + 5, (PyObject *)*def.type) < 0)
            return -1;
    }
    return 0;
}
PyObject *idl_spec_for(PyObject *owner)
{
    PyObject *spec = PyObject_GetAttrString(owner, "__v8_idl_spec__");
    if (spec == nullptr)
    {
        if (PyErr_ExceptionMatches(PyExc_AttributeError))
            PyErr_Clear();
        return nullptr;
    }
    if (Py_TYPE((PyObject*)spec) != idl_spec_type)
    {
        Py_DECREF((PyObject*)spec);
        PyErr_SetString(PyExc_TypeError, "__v8_idl_spec__ must be an IdlSpec");
        return nullptr;
    }
    return spec;
}
PyObject *idl_spec_resolve(PyObject *original, PyObject *owner)
{
    bool resolved = Py_TYPE((PyObject*)original) == idl_spec_type ? ((IdlSpec *)original)->callbacks_resolved
                    : Py_TYPE((PyObject*)original) == attribute_spec_type
                        ? ((AttributeSpec *)original)->callbacks_resolved
                        : ((MethodSpec *)original)->callbacks_resolved;
    if (resolved)
    {
        Py_INCREF((PyObject*)original);
        return original;
    }
    PyObject *changes = nullptr;
    auto &def = definition(Py_TYPE((PyObject*)original));
    for (size_t index = 0; index < def.count; ++index)
    {
        const Field &field = def.fields[index];
        if (field.kind != Kind::Callback && field.kind != Kind::RequiredCallback)
            continue;
        PyObject *reference = *(PyObject **)address(original, field);
        if (reference == nullptr || !PyUnicode_Check(reference))
            continue;
        if (changes == nullptr)
        {
            changes = PyDict_New();
            if (changes == nullptr)
                return nullptr;
        }
        PyObject *callback = PyObject_GetAttr(owner, reference);
        if (callback == nullptr)
        {
            Py_DECREF((PyObject*)changes);
            return nullptr;
        }
        if (!PyCallable_Check(callback))
        {
            Py_DECREF((PyObject*)callback);
            Py_DECREF((PyObject*)changes);
            PyErr_SetString(PyExc_TypeError, "IDL callback is not callable");
            return nullptr;
        }
        int result = PyDict_SetItemString(changes, field.name, callback);
        Py_DECREF((PyObject*)callback);
        if (result < 0)
        {
            Py_DECREF((PyObject*)changes);
            return nullptr;
        }
    }
    PyObject *result;
    if (changes == nullptr)
    {
        Py_INCREF((PyObject*)original);
        result = original;
    }
    else
        result = idl_spec_replace(original, changes);
    Py_XDECREF((PyObject*)changes);
    return result;
}
bool idl_spec_flag(PyObject *owner, const char *key, bool *result)
{
    PyObject *value = idl_spec_for(owner);
    if (value == nullptr)
    {
        *result = false;
        return !PyErr_Occurred();
    }
    IdlSpec *spec = (IdlSpec *)value;
    *result = std::strcmp(key, "hidden") == 0 ? spec->hidden : spec->skip_inherit;
    Py_DECREF((PyObject*)value);
    return true;
}
