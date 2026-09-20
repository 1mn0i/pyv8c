#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "jsfunction.h"
#include "jsobject.h"
#include "convert.h"
#include "context.h"
#include "timeout_guard.h"

using namespace v8;

PyTypeObject *js_function_type = NULL;

static PyType_Slot js_function_slots[] = {
    {Py_tp_dealloc, (void*)PythonBoundary<js_function_dealloc>::call},
    {Py_tp_repr, (void*)PythonBoundary<js_function_repr>::call},
    {Py_tp_call, (void*)PythonBoundary<js_function_call>::call},
    {Py_tp_doc, (void*)""},
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
    Py_INCREF((PyObject*)js_object_type);

    js_function_type = (PyTypeObject * )PyType_FromSpecWithBases(&js_function_spec, bases);
    Py_DECREF((PyObject*)bases);

    if (js_function_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *js_function_call(js_function *self, PyObject *args, PyObject *kwargs) {
    if (!js_object_require_active(&self->base))
        return NULL;
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
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
    V8ValueArguments argv(argc);
    if (!argv.valid())
    {
        PyErr_NoMemory();
        return NULL;
    }
    jss_from_pys(args, argv.data(), context);
    if (PyErr_Occurred()) return NULL;
    if (!timeout_guard.start()) return NULL;
    result = object->CallAsFunction(context, js_this, argc, argv.data());
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
    if (!js_object_require_active(&self->base))
        return NULL;
    ExecutionScope execution(self->base.registration.context, (PyObject*)self);
    if (!execution || !js_object_require_active(&self->base)) return nullptr;
    auto* isolate = execution.isolate();
    Local<Object> object = self->base.object.Get(isolate);
    IN_CONTEXT(object->GetCreationContextChecked());
    JS_TRY;
    TimeoutGuard timeout_guard(context);
    if (!timeout_guard.start())
        return NULL;
    PyObject* repr = NULL;
    if (object->IsFunction()) {
        Local<Object> global = context->Global();
        v8::Local<v8::Value> faker, raw_tostring, ret;
        v8::MaybeLocal<v8::Value> maybe_faker, maybe_raw_tostring, maybe_ret;
        maybe_faker = global->Get(
            context, self->base.isolate_state->v8_string(V8StringId::Faker));
        if (maybe_faker.ToLocal(&faker) && faker->IsObject()) {
            maybe_raw_tostring = faker.As<v8::Object>()->Get(
                context, self->base.isolate_state->v8_string(V8StringId::RawToString));
            if (maybe_raw_tostring.ToLocal(&raw_tostring) && raw_tostring->IsFunction()) {
                maybe_ret = raw_tostring.As<v8::Function>()->Call(context, object, 0, NULL);
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

    if (!timeout_guard.stop())
        return NULL;

    PY_PROPAGATE_JS;
    return repr;
}

void js_function_dealloc(js_function *self) {
    js_object_dealloc((js_object*)self);
}
