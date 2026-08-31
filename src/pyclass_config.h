#ifndef PYCLASS_CONFIG_H
#define PYCLASS_CONFIG_H

#include <Python.h>
#include "context.h"

struct ClassConfig {
    PyObject* class_dict = NULL;
    PyObject* length = NULL;
    PyObject* attributes = NULL;
    PyObject* methods = NULL;
    PyObject* constant_values = NULL;
    PyObject* constant_callbacks = NULL;
    PyObject* call = NULL;
    PyObject* array = NULL;
    PyObject* immutable = NULL;
    PyObject* has_constructor = NULL;
    PyObject* constructor_behavior = NULL;
    PyObject* constructor_adapter = NULL;
    PyObject* callback_owner = NULL;
    PyObject* parent_class = NULL;
    PyObject* to_string_tag = NULL;
    bool global_this = false, undetectable = false, as_object = false, unconstructable = false;
    bool name_get = false, name_set = false, name_query = false, name_enum = false, name_del = false;
    bool index_get = false, index_set = false, index_query = false, index_enum = false, index_del = false;
    bool cross_origin_check = false;
    bool cross_origin_name_get = false, cross_origin_name_set = false, cross_origin_name_query = false;
    bool cross_origin_name_enum = false, cross_origin_name_del = false;
    bool cross_origin_index_get = false, cross_origin_index_set = false, cross_origin_index_query = false;
    bool cross_origin_index_enum = false, cross_origin_index_del = false;

    ~ClassConfig();
};

bool class_config_load(PyObject* cls, context_c* py_ctx, ClassConfig* config);

#endif

