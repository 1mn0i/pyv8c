
#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "jsobject.h"
#include "context.h"
#include "jsfunction.h"
#include "jspromise.h"
#include "jstypedarray.h"
#include "timeout_guard.h"

using namespace v8;


static PyObject* js_object_read(js_object* self, PyObject* name) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    Local<Value> key = js_from_py(name, context);
    if (key.IsEmpty() || PyErr_Occurred()) return nullptr;
    JS_TRY
    TimeoutGuard guard(context);
    if (!guard.start()) return nullptr;
    Local<Value> value;
    bool success = object->Get(context, key).ToLocal(&value);
    if (!guard.stop()) return nullptr;
    PY_PROPAGATE_JS;
    if (!success) return nullptr;
    return py_argument_from_js(value, context);
}

static PyObject* js_object_to_python(js_object* self, PyObject*) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    PyObject* result = py_from_js(object, context);
    PY_PROPAGATE_JS;
    return result;
}

static PyObject* js_object_string(js_object* self, PyObject*) {
    return js_object_repr(self);
}

static PyObject* js_object_number(js_object* self, PyObject*) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    TimeoutGuard guard(context);
    if (!guard.start()) return nullptr;
    Local<Number> value;
    bool success = object->ToNumber(context).ToLocal(&value);
    if (!guard.stop()) return nullptr;
    PY_PROPAGATE_JS;
    if (!success) return nullptr;
    return PyFloat_FromDouble(value->Value());
}

static PyObject* js_object_iterator_method(js_object* self, PyObject*) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    TimeoutGuard guard(context);
    if (!guard.start()) return nullptr;
    Local<Value> result;
    bool success = object->Get(context, Symbol::GetIterator(isolate)).ToLocal(&result);
    if (!guard.stop()) return nullptr;
    PY_PROPAGATE_JS;
    if (!success) return nullptr;
    return py_argument_from_js(result, context);
}

static PyObject* js_object_invoke(js_object* self, PyObject* args) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    if (!object->IsCallable() || PyTuple_Size(args) < 1) {
        PyErr_SetString(PyExc_TypeError, "_invoke requires a callable and a receiver");
        return nullptr;
    }
    const int length = (int)PyTuple_Size(args);
    V8ValueArguments converted(length);
    if (!converted.valid()) { PyErr_NoMemory(); return nullptr; }
    jss_from_pys(args, converted.data(), context);
    if (PyErr_Occurred()) return nullptr;
    JS_TRY
    TimeoutGuard guard(context);
    if (!guard.start()) return nullptr;
    Local<Value> result;
    bool success = object->CallAsFunction(context, converted.data()[0], length - 1, converted.data() + 1).ToLocal(&result);
    if (!guard.stop()) return nullptr;
    PY_PROPAGATE_JS;
    if (!success) return nullptr;
    return py_argument_from_js(result, context);
}

static PyObject* js_object_keys(js_object* self, PyObject*) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    TimeoutGuard guard(context);
    if (!guard.start()) return nullptr;
    Local<Array> keys;
    bool success = object->GetOwnPropertyNames(context,
        static_cast<PropertyFilter>(ONLY_ENUMERABLE | SKIP_SYMBOLS), KeyConversionMode::kConvertToString).ToLocal(&keys);
    if (!guard.stop()) return nullptr;
    PY_PROPAGATE_JS;
    if (!success) return nullptr;
    return py_from_js(keys, context);
}

static PyObject* js_object_is_array(js_object* self, PyObject*) {
    if (!js_object_require_active(self)) return nullptr;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    return PyBool_FromLong(self->object.Get(isolate)->IsArray());
}

PyMethodDef js_object_methods[] = {
    {"_iterator_method", (PyCFunction)js_object_iterator_method, METH_NOARGS, nullptr},
    {"_invoke", (PyCFunction)js_object_invoke, METH_VARARGS, nullptr},
    {"_keys", (PyCFunction)js_object_keys, METH_NOARGS, nullptr},
    {"_is_array", (PyCFunction)js_object_is_array, METH_NOARGS, nullptr},
    {"_to_string", (PyCFunction)js_object_string, METH_NOARGS, nullptr},
    {"_to_number", (PyCFunction)js_object_number, METH_NOARGS, nullptr},
    {"_get", (PyCFunction)js_object_read, METH_O, "Read one property without eagerly converting objects."},
    {"_to_python", (PyCFunction)js_object_to_python, METH_NOARGS, "Explicitly materialize an object using the default conversion."},
    {"__dir__", (PyCFunction)js_object_dir, METH_NOARGS, NULL},
    {NULL} };


static PyType_Slot js_object_slots[] = {
    {Py_tp_traverse, (void*)js_object_traverse},
    {Py_tp_clear, (void*)js_object_clear},
    {Py_tp_getattro, (void*)js_object_getattro},    // __getattribute__
    {Py_tp_setattro, (void*)js_object_setattro},    // __setattribute__
    {Py_mp_subscript, (void*)js_object_getattro},  // __getitem__
    {Py_mp_ass_subscript, (void*)js_object_setattro},  // __setitem__
    {Py_tp_iter, (void*)js_object_getiter},
    {Py_tp_richcompare, (void*)js_object_richcompare},
    {Py_tp_dealloc, (void*)js_object_dealloc},
    {Py_tp_repr, (void*)js_object_repr},
    {Py_tp_methods, js_object_methods},
    {Py_tp_doc, (void*)"v8 Object"},
    {0, NULL} };

static PyType_Spec js_object_spec = {
    "_pyv8.JSObject",
    sizeof(js_object),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC,
    js_object_slots };


PyTypeObject *js_object_type = NULL;

bool
js_object_require_active(const js_object* self)
{
    if (self == nullptr || self->isolate_state == nullptr || self->object.IsEmpty() ||
        (self->registry_context && !context_is_active((context_c*)self->registry_context))) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript object has no active V8 isolate");
        return false;
    }
    return self->isolate_state->require_active();
}

static bool
js_object_should_try_python_attribute(js_object* self, PyObject* name)
{
    if (Py_TYPE(self) != js_object_type &&
        Py_TYPE(self) != js_function_type &&
        Py_TYPE(self) != js_promise_type &&
        Py_TYPE(self) != js_typedarray_type)
    {
        return true;
    }
    if (!PyUnicode_Check(name))
        return false;

    Py_ssize_t length;
    const char* utf8_name = PyUnicode_AsUTF8AndSize(name, &length);
    if (utf8_name == NULL)
        return true;
    return length == 0 || utf8_name[0] == '_';
}

void
js_object_set_persistent(js_object* self, Local<Object> object,
                         Local<Context> context, bool retain_context)
{
    Local<Context> creation;
    if (object->GetCreationContext().ToLocal(&creation)) context = creation;
    Isolate* isolate = v8_compat::context_isolate(context);
    IsolateState* isolate_state = runtime_manager.find_isolate(isolate);
    self->isolate_state = isolate_state;
    self->object.Reset(isolate, object);
    isolate_state->retain_v8_handle();
    if (!context.IsEmpty()) {
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            context_c* py_context = (context_c*)v8_compat::external_value(context_data);
            if (py_context == nullptr) return;
            self->registry_context = (PyObject*)py_context;
            context_register_handle(py_context, (PyObject*)self, (int (*)(PyObject*))js_object_clear);
            if (retain_context) {
                self->py_context = (PyObject*)py_context;
                Py_INCREF(self->py_context);
            }
            if (py_context->py_isolate != nullptr) {
                self->py_isolate = py_context->py_isolate;
                Py_INCREF(self->py_isolate);
            }
        }
    }
}

int js_object_type_init()
{
    js_object_type = (PyTypeObject*)PyType_FromSpec(&js_object_spec);
    if (js_object_type == NULL)
    {
        return -1;
    }
    return 0;
}


js_object *
js_object_new(Local<Object> object, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_object *self;
    
    if (object->IsPromise()) {
        //self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_promise_type, 0);
    } else if (object->IsCallable()) {
        //self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
        self = (js_object *)PyType_GenericAlloc(js_function_type, 0);
    } else if (object->IsTypedArray()) {
        //self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_typedarray_type, 0);
    } else {
        //self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_object_type, 0);
    }

    if (self != NULL) {
        js_object_set_persistent(self, object, context, true);
    }
    return self;
}

void 
js_object_weak_callback(const WeakCallbackInfo<js_object> &info)
{
    js_object *self = info.GetParameter();
    if (!self->object.IsEmpty()) {
        self->object.Reset();
        self->isolate_state->release_v8_handle();
    }
    self->weak_owned = false;
    self->isolate_state->defer_release((PyObject*)self);
}

js_object *
js_object_weak_new(Local<Object> object, Local<Context> context)
{
    IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
    IN_ISOLATE(isolate_state);
    Context::Scope cs(context);
    js_object *self;
    if (object->IsPromise()) {
        //self = (js_object *) js_promise_type.tp_alloc(&js_promise_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_promise_type, 0);
    }
    else if (object->IsCallable()) {
        //self = (js_object *) js_function_type.tp_alloc(&js_function_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_function_type, 0);
    }
    else if (object->IsTypedArray()) {
        //self = (js_object*)js_typedarray_type.tp_alloc(&js_typedarray_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_typedarray_type, 0);
    }
    else {
        //self = (js_object *) js_object_type.tp_alloc(&js_object_type, 0);
        self = (js_object*)PyType_GenericAlloc(js_object_type, 0);
    }

    if (self != NULL) {
        js_object_set_persistent(self, object, context, false);
        // The V8 weak callback owns this reference and releases it on GC.
        Py_INCREF(self);
        self->weak_owned = true;
        //self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kFinalizer);
        self->object.SetWeak(self, js_object_weak_callback, WeakCallbackType::kParameter);
    }
    return self;
}

PyObject *
js_object_getattro(js_object *self, PyObject *name)
{
    if (js_object_should_try_python_attribute(self, name))
    {
        PyObject *attr_value = PyObject_GenericGetAttr((PyObject *)self, name);
        if (attr_value == NULL)
            PyErr_Clear();
        else
            return attr_value;
    }

    if (!js_object_require_active(self))
        return NULL;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    Local<Value> js_name = js_from_py(name, context);
    if (PyErr_Occurred()) return NULL;
    if (js_name.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "failed to convert JavaScript property name");
        return NULL;
    }
    JS_TRY
    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return NULL;
    Maybe<bool> maybe_has_property = object->Has(context, js_name);
    if (!timeout_guard.stop()) return NULL;
    PY_PROPAGATE_JS;
    bool has_property;
    if (!maybe_has_property.To(&has_property)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to check JavaScript property");
        return NULL;
    }
    if (!has_property) {
        // TODO fix this so that it works
        PyObject *class_name = py_from_js(object->GetConstructorName(), context);
        PyErr_PROPAGATE(class_name);
        PyObject *class_name_string = PyObject_Str(class_name);
        Py_XDECREF(class_name);
        PyErr_PROPAGATE(class_name_string);

        PyErr_Format(PyExc_AttributeError, "'%.50S' JavaScript object has no attribute '%.400S'", 
                class_name_string, name);
        Py_XDECREF(class_name_string);
        return NULL;
    }

    if (!timeout_guard.start()) return NULL;
    MaybeLocal<Value> js_value = object->Get(context, js_name);
    if (!timeout_guard.stop()) return NULL;

    PY_PROPAGATE_JS;
    Local<Value> local_js_value;
    if (!js_value.ToLocal(&local_js_value)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property");
        return NULL;
    }
    PyObject *value = py_from_js(local_js_value, context);
    PyErr_PROPAGATE(value);
    // if this was called like object.method() then bind the return value to make it callable
    if (Py_TYPE(value) == js_function_type) {
        js_function *func = (js_function *) value;

        func->js_this.Reset(isolate, object);
    }
    return value;
}

int 
js_object_setattro(js_object *self, PyObject *name, PyObject *value)
{
    if (js_object_should_try_python_attribute(self, name))
    {
        PyObject *attr_value = PyObject_GenericGetAttr((PyObject *)self, name);
        if (attr_value == NULL)
        {
            PyErr_Clear();
        }
        else
        {
            Py_XDECREF(attr_value);
            return PyObject_GenericSetAttr((PyObject *)self, name, value);
        }
    }

    if (!js_object_require_active(self))
        return -1;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return -1;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY
    Local<Value> js_name = js_from_py(name, context);
    if (PyErr_Occurred()) return -1;
    Local<Value> js_value;
    if (value != NULL) {
        js_value = js_from_py(value, context);
        if (PyErr_Occurred()) return -1;
    }

    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return -1;

    Maybe<bool> maybe_worked = value != NULL
        ? object->Set(context, js_name, js_value)
        : object->Delete(context, js_name);

    if (!timeout_guard.stop()) return -1;

    PY_PROPAGATE_JS_RET(-1);
    bool worked;
    if (!maybe_worked.To(&worked)) {
        PY_PROPAGATE_JS_RET(-1);
        PyErr_SetString(PyExc_RuntimeError, "failed to set JavaScript property");
        return -1;
    }
    return 0;
}

PyObject *
js_object_getiter(js_object *self)
{
    PyObject *props = js_object_dir(self);
    PyErr_PROPAGATE(props);
    PyObject *iter = PySeqIter_New(props);
    Py_XDECREF(props);
    return iter;
}

PyObject *
js_object_dir(js_object *self)
{
    if (!js_object_require_active(self))
        return NULL;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    Local<Context> context = object->GetCreationContextChecked();
    Context::Scope cs(context);
    JS_TRY

    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start()) return NULL;
    MaybeLocal<Array> maybe_properties = object->GetOwnPropertyNames(context, ALL_PROPERTIES);
    if (!timeout_guard.stop()) return NULL;
    PY_PROPAGATE_JS;
    Local<Array> properties;
    if (!maybe_properties.ToLocal(&properties)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property names");
        return NULL;
    }
    PyObject *py_properties = PyList_New(properties->Length());
    PyErr_PROPAGATE(py_properties);

    if (!timeout_guard.start()) {
        Py_DECREF(py_properties);
        return NULL;
    }
    for (unsigned i = 0; i < properties->Length(); i++) {
        MaybeLocal<Value> js_property = properties->Get(context, i);
        Local<Value> local_js_property;
        if (!js_property.ToLocal(&local_js_property)) {
            timeout_guard.stop();
            Py_DECREF(py_properties);
            PY_PROPAGATE_JS;
            PyErr_SetString(PyExc_RuntimeError, "failed to get JavaScript property name");
            return NULL;
        }
        PyObject *py_property = py_from_js(local_js_property, context);
        if (py_property == NULL) {
            timeout_guard.stop();
            Py_DECREF(py_properties);
            return NULL;
        }
        if (PyList_SetItem(py_properties, i, py_property) < 0) {
            timeout_guard.stop();
            Py_DECREF(py_property);
            Py_DECREF(py_properties);
            return NULL;
        }
    }

    if (!timeout_guard.stop()) {
        Py_DECREF(py_properties);
        return NULL;
    }

    return py_properties;
}

PyObject *
js_object_richcompare(js_object* self, PyObject* other, int operation)
{
    if (!PyObject_TypeCheck(other, js_object_type)) {
        PYV8_RETURN_NOTIMPLEMENTED;
    }

    if (operation != Py_EQ && operation != Py_NE) {
        PYV8_RETURN_NOTIMPLEMENTED;
    }

    js_object* other_object = (js_object*)other;
    if (!js_object_require_active(self) || !js_object_require_active(other_object)) {
        return NULL;
    }
    if (self->isolate_state != other_object->isolate_state) {
        if (operation == Py_NE)
            PYV8_RETURN_TRUE;
        PYV8_RETURN_FALSE;
    }
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    JS_TRY;

    Local<Object> js_obj = self->object.Get(isolate);
    Local<Object> js_obj_other = other_object->object.Get(isolate);
    bool equals = !js_obj_other.IsEmpty() && !js_obj.IsEmpty() &&
                  js_obj->StrictEquals(js_obj_other);
    if (operation == Py_NE)
        equals = !equals;
    return PyBool_FromLong(equals ? 1 : 0);
}

PyObject *
js_object_repr(js_object *self)
{
    if (!js_object_require_active(self))
        return NULL;
    IN_ISOLATE(self->isolate_state);
    if (!js_object_require_active(self)) return nullptr;
    Local<Object> object = self->object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start())
        return NULL;
    Local<String> v8_str;
    MaybeLocal<String> maybe_v8_str = object->ToString(context);
    if (!timeout_guard.stop())
        return NULL;
    if (!maybe_v8_str.ToLocal(&v8_str)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "JavaScript ToString failed");
        return nullptr;
    }

    v8::String::Utf8Value str(isolate, v8_str);
    PyObject* repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    PY_PROPAGATE_JS;
    return repr;
}

int js_object_traverse(js_object* self, visitproc visit, void* arg) {
    Py_VISIT(self->py_context);
    Py_VISIT(self->py_isolate);
    return 0;
}

int js_object_clear(js_object* self)
{
    auto* state = self->isolate_state;
    std::unique_ptr<IsolateLockScope> lock;
    if (state != nullptr) lock.reset(new IsolateLockScope(state));
    context_unregister_handle((context_c*)self->registry_context, (PyObject*)self);
    self->registry_context = nullptr;
    if (PyObject_TypeCheck((PyObject*)self, js_function_type))
        ((js_function*)self)->js_this.Reset();
    if (!self->object.IsEmpty()) {
        self->object.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    if (self->weak_owned) {
        self->weak_owned = false;
        state->defer_release((PyObject*)self);
    }
    self->isolate_state = nullptr;
    Py_CLEAR(self->py_context);
    Py_CLEAR(self->py_isolate);
    return 0;
}

void js_object_dealloc(js_object *self)
{
    PyObject_GC_UnTrack(self);
    js_object_clear(self);
    TP_FREE_ABI3(js_object_type, self);
}
