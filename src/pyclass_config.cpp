#include "pyv8.h"
#include "pyclass_config.h"

ClassConfig::~ClassConfig()
{
    Py_XDECREF(class_dict); Py_XDECREF(length); Py_XDECREF(attributes); Py_XDECREF(methods);
    Py_XDECREF(constant_values); Py_XDECREF(constant_callbacks); Py_XDECREF(call); Py_XDECREF(array);
    Py_XDECREF(immutable); Py_XDECREF(has_constructor); Py_XDECREF(constructor_behavior);
    Py_XDECREF(constructor_adapter); Py_XDECREF(callback_owner); Py_XDECREF(parent_class);
    Py_XDECREF(to_string_tag);
}

static bool class_config_has(PyObject* class_dict, const char* name, bool* result)
{
    int has_key = PyMapping_HasKeyString(class_dict, name);
    if (has_key < 0) return false;
    *result = has_key != 0;
    return true;
}

static bool class_config_get(PyObject* class_dict, const char* name, PyObject** result)
{
    bool has_key = false;
    if (!class_config_has(class_dict, name, &has_key)) return false;
    if (!has_key) return true;
    *result = PyMapping_GetItemString(class_dict, name);
    return *result != NULL;
}

static bool class_config_get_with_overlay(PyObject* class_dict, PyObject* overlay,
                                          const char* name, PyObject** result)
{
    if (overlay != NULL) {
        bool has_key = false;
        if (!class_config_has(overlay, name, &has_key)) return false;
        if (has_key) {
            *result = PyMapping_GetItemString(overlay, name);
            return *result != NULL;
        }
    }
    return class_config_get(class_dict, name, result);
}

static bool class_config_has_with_overlay(PyObject* class_dict, PyObject* overlay,
                                          const char* name, bool* result)
{
    if (overlay != NULL) {
        bool has_key = false;
        if (!class_config_has(overlay, name, &has_key)) return false;
        if (has_key) { *result = true; return true; }
    }
    return class_config_has(class_dict, name, result);
}

bool class_config_load(PyObject* cls, context_c* py_ctx, ClassConfig* config)
{
    config->class_dict = PyObject_GetAttrString(cls, "__dict__");
    if (config->class_dict == NULL) return false;
    PyObject* overlay = NULL;
    if (py_ctx->class_configs != NULL) {
        overlay = PyDict_GetItemWithError(py_ctx->class_configs, cls);
        if (overlay == NULL && PyErr_Occurred()) return false;
        if (overlay != NULL && !PyMapping_Check(overlay)) {
            PyErr_SetString(PyExc_TypeError, "class_configs values must be mappings");
            return false;
        }
        // Promote the borrowed dict entry to an owned reference for the rest
        // of this function.  The commit path (Context.update_configs) may
        // replace an overlay for the same class while this in-flight
        // ClassConfig is being filled; keeping our own reference guarantees
        // the struct never points at a recycled value object.
        Py_XINCREF(overlay);
    }
    if (!class_config_get_with_overlay(config->class_dict, overlay, "__v8_length__", &config->length) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_attribute__", &config->attributes) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_method__", &config->methods) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_constants_value__", &config->constant_values) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_constants_callback__", &config->constant_callbacks) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__call__", &config->call) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_array__", &config->array) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_immutable__", &config->immutable) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_has_constructor__", &config->has_constructor) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_constructor__", &config->constructor_behavior) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_constructor_adapter__", &config->constructor_adapter) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_callback_owner__", &config->callback_owner) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_parent_class__", &config->parent_class) ||
        !class_config_get_with_overlay(config->class_dict, overlay, "__v8_to_string_tag__", &config->to_string_tag)) goto config_load_failed;

#define CONFIG_FLAG(field, key) !class_config_has_with_overlay(config->class_dict, overlay, key, &config->field)
    if (CONFIG_FLAG(global_this, "__v8_global_this__") || CONFIG_FLAG(undetectable, "__v8_undetectable__") || CONFIG_FLAG(as_object, "__v8_as_obj__") ||
        CONFIG_FLAG(unconstructable, "__v8_unconstructable__") || CONFIG_FLAG(name_get, "__v8_name_get__") || CONFIG_FLAG(name_set, "__v8_name_set__") ||
        CONFIG_FLAG(name_query, "__v8_name_query__") || CONFIG_FLAG(name_enum, "__v8_name_enum__") || CONFIG_FLAG(name_del, "__v8_name_del__") ||
        CONFIG_FLAG(index_get, "__v8_index_get__") || CONFIG_FLAG(index_set, "__v8_index_set__") || CONFIG_FLAG(index_query, "__v8_index_query__") ||
        CONFIG_FLAG(index_enum, "__v8_index_enum__") || CONFIG_FLAG(index_del, "__v8_index_del__") || CONFIG_FLAG(cross_origin_check, "__v8_cross_origin_check__") ||
        CONFIG_FLAG(cross_origin_name_get, "__v8_cross_origin_name_get__") || CONFIG_FLAG(cross_origin_name_set, "__v8_cross_origin_name_set__") ||
        CONFIG_FLAG(cross_origin_name_query, "__v8_cross_origin_name_query__") || CONFIG_FLAG(cross_origin_name_enum, "__v8_cross_origin_name_enum__") ||
        CONFIG_FLAG(cross_origin_name_del, "__v8_cross_origin_name_del__") || CONFIG_FLAG(cross_origin_index_get, "__v8_cross_origin_index_get__") ||
        CONFIG_FLAG(cross_origin_index_set, "__v8_cross_origin_index_set__") || CONFIG_FLAG(cross_origin_index_query, "__v8_cross_origin_index_query__") ||
        CONFIG_FLAG(cross_origin_index_enum, "__v8_cross_origin_index_enum__") || CONFIG_FLAG(cross_origin_index_del, "__v8_cross_origin_index_del__")) goto config_load_failed;
#undef CONFIG_FLAG
    Py_XDECREF(overlay);
    return true;

config_load_failed:
    // class_dict and any partially filled members are owned by the caller's
    // ClassConfig and released by its destructor; only overlay is local.
    Py_XDECREF(overlay);
    return false;
}
