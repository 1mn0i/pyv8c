#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "jsfunction.h"
#include "jsobject.h"
#include "convert.h"
#include "context.h"

using namespace v8;

PyTypeObject *js_function_type = NULL;

static PyType_Slot js_function_slots[] = {
    {Py_tp_dealloc, (void*)js_function_dealloc},
    {Py_tp_repr, (void*)js_function_repr},
    {Py_tp_call, (void*)js_function_call},
    {Py_tp_doc, ""},
    {0, NULL} };

static PyType_Spec js_function_spec = {
    "_pyv8.BoundFunction",
    sizeof(js_function),
    0,
    Py_TPFLAGS_DEFAULT,
    js_function_slots };

int js_function_type_init()
{
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    PyTuple_SetItem(bases, 0, (PyObject*)js_object_type);
    Py_INCREF(js_object_type); 

    js_function_type = (PyTypeObject * )PyType_FromSpecWithBases(&js_function_spec, bases);
    Py_DECREF(bases);

    if (js_function_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *js_function_call(js_function *self, PyObject *args, PyObject *kwargs) {
    IN_V8;
    Local<Object> object = self->base.object.Get(isolate);
    //IN_CONTEXT(object->CreationContext());
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY

    Local<Value> js_this;
    if (self->js_this.IsEmpty()) {
        js_this = Undefined(isolate);
    } else {
        js_this = self->js_this.Get(isolate);
    }
    int argc = PyTuple_Size(args);
    MaybeLocal<Value> result;
    TimeoutGuard timeout_guard(context);

#ifndef _WIN32
    // error C2131: expression did not evaluate to a constant on Windows
    if (argc <= 16) {
        Local<Value> argv[16];
        jss_from_pys(args, argv, context);
        if (PyErr_Occurred()) return NULL;
        if (!timeout_guard.start()) return NULL;
        result = object->CallAsFunction(context, js_this, argc, argv);
    } else {
#endif
        Local<Value> *argv = new Local<Value>[argc];
        jss_from_pys(args, argv, context);
        if (PyErr_Occurred()) {
            delete[] argv;
            return NULL;
        }
        if (!timeout_guard.start()) {
            delete[] argv;
            return NULL;
        }
        result = object->CallAsFunction(context, js_this, argc, argv);
        delete[] argv;
#ifndef _WIN32
    }
#endif
    if (!timeout_guard.stop()) return NULL;
    PY_PROPAGATE_JS;
    Local<Value> js_result;
    if (!result.ToLocal(&js_result)) {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to call JavaScript function");
        return NULL;
    }
    return py_from_js(js_result, context);
}


PyObject* js_function_repr(js_function* self) {
    IN_V8;
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    PyObject* repr = NULL;
    if (object->IsFunction()) {
        Local<Object> global = context->Global();
        v8::Local<v8::Value> faker, raw_tostring, ret;
        v8::MaybeLocal<v8::Value> maybe_faker, maybe_raw_tostring, maybe_ret;
        maybe_faker = global->Get(context, V8String(isolate, HOOKHANDLE));
        if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
            maybe_raw_tostring = faker.As<v8::Object>()->Get(context, V8String(isolate, "raw_tostring"));
            if (maybe_raw_tostring.ToLocal(&raw_tostring) && raw_tostring->IsFunction()) {
                v8::MaybeLocal<v8::Value> maybe_ret = raw_tostring.As<v8::Function>()->Call(context, object, 0, NULL);
                if (maybe_ret.ToLocal(&ret)) {
                    String::Utf8Value str(isolate, ret);
                    repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
                }
            }
        }
    }
    if (repr == NULL) {
        String::Utf8Value str(isolate, object);
        //String::Utf8Value str(isolate, object->ToString(context).ToLocalChecked());
        repr = PyUnicode_DecodeUTF8(*str, str.length(), NULL);
    }

    PY_PROPAGATE_JS;
    return repr;
}

void js_function_dealloc(js_function *self) {
    self->js_this.Reset();
    js_object_dealloc((js_object*)self);
}
