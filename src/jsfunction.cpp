#include <Python.h>
#include "pyv8env.h"
#include <v8.h>

#include "jsobject.h"
#include "convert.h"
#include "context.h"

using namespace v8;

PyTypeObject js_function_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int js_function_type_init() {
    js_function_type.tp_name = "pyv8env.BoundFunction";
    js_function_type.tp_basicsize = sizeof(js_function);
    js_function_type.tp_dealloc = (destructor) js_function_dealloc;
    js_function_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_function_type.tp_doc = "";
    js_function_type.tp_repr = (reprfunc) js_function_repr;
    js_function_type.tp_call = (ternaryfunc) js_function_call;
    js_function_type.tp_base = &js_object_type;
    return PyType_Ready(&js_function_type);
}

PyObject *js_function_call(js_function *self, PyObject *args, PyObject *kwargs) {
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
    //IN_CONTEXT(object->CreationContext());
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY

    Local<Value> js_this;
    if (self->js_this.IsEmpty()) {
        js_this = Undefined(isolate);
    } else {
        js_this = self->js_this.Get(isolate);
    }
    int argc = PyTuple_GET_SIZE(args);
    MaybeLocal<Value> result;

    if (!context_setup_timeout(context)) return NULL;
#ifndef _WIN32
    // error C2131: expression did not evaluate to a constant on Windows
    if (argc <= 16) {
        Local<Value> argv[argc];
        jss_from_pys(args, &argv[0], context);
        result = object->CallAsFunction(context, js_this, argc, &argv[0]);
    } else {
#endif
        Local<Value> *argv = new Local<Value>[argc];
        jss_from_pys(args, argv, context);
        result = object->CallAsFunction(context, js_this, argc, argv);
        delete[] argv;
#ifndef _WIN32
    }
#endif
    if (!context_cleanup_timeout(context)) return NULL;
    PY_PROPAGATE_JS;
    return py_from_js(result.ToLocalChecked(), context);
}


PyObject* js_function_repr(js_function* self) {
    IN_V8;
    Local<Object> object = self->object.Get(isolate);
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
    js_object_dealloc((js_object *) self);
}
