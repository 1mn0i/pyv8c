#include <Python.h>
#include "pyv8env.h"
#include <v8.h>
#include <sstream>

#include "script.h"
#include "pyclass.h"
#include "convert.h"

PyGetSetDef js_exception_getsets[] = {
    {"value", (getter) js_exception_get_value, NULL, NULL},
    {NULL},
};
PyTypeObject js_exception_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int js_exception_type_init() {
    js_exception_type.tp_name = "pyv8env.JSException";
    js_exception_type.tp_base = (PyTypeObject *) PyExc_Exception;
    js_exception_type.tp_basicsize = sizeof(js_exception);
    js_exception_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_exception_type.tp_doc = "v8 Exception";
    js_exception_type.tp_getset = js_exception_getsets;
    //js_exception_type.tp_init = (initproc)js_exception_init;
    //js_exception_type.tp_str = (reprfunc)js_exception_str;

    js_exception_type.tp_dealloc = (destructor) js_exception_dealloc;
    return PyType_Ready(&js_exception_type);
}

const std::string extract_exception(Local<Value> exception, Local<Message> message)
{
    assert(isolate->InContext());

    v8::HandleScope handle_scope(isolate);

    std::ostringstream oss;

    v8::String::Utf8Value msg(isolate, exception);

    if (*msg)
        oss << std::string(*msg, msg.length());

    if (!message.IsEmpty())
    {
        oss << " ( ";

        if (!message->GetScriptResourceName().IsEmpty() &&
            !message->GetScriptResourceName()->IsUndefined())
        {
            v8::String::Utf8Value name(isolate, message->GetScriptResourceName());
            oss << std::string(*name, name.length());
        }

        oss << " @ " << message->GetLineNumber(isolate->GetCurrentContext()).ToChecked()
            << " : " << message->GetStartColumn() << " ) ";

        if (!message->GetSourceLine(isolate->GetCurrentContext()).IsEmpty())
        {
            v8::String::Utf8Value line(isolate, message->GetSourceLine(isolate->GetCurrentContext()).ToLocalChecked());

            oss << " -> " << std::string(*line, line.length());
        }

        oss << "\n";
        Local<StackTrace> stack_trace = message->GetStackTrace();
        for (int i = 0; i < stack_trace->GetFrameCount(); i++)
        {
            v8::Handle<v8::StackFrame> frame = stack_trace->GetFrame(isolate, i);
            v8::String::Utf8Value funcName(isolate, frame->GetFunctionName()), scriptName(isolate, frame->GetScriptName());
            oss << "\tat ";
            if (funcName.length())
                oss << std::string(*funcName, funcName.length()) << " (";

            if (frame->IsEval())
            {
                oss << "(eval)";
            }
            else
            {
                oss << std::string(*scriptName, scriptName.length()) << ":"
                    << frame->GetLineNumber() << ":" << frame->GetColumn();
            }

            if (funcName.length())
                oss << ")";

            oss << std::endl;
        }
    }

    return oss.str();
}

PyObject *js_exception_new(Local<Value> exception, Local<Message> message) {
    HandleScope hs(isolate);
    Local<Context> no_ctx;
    js_exception *self = (js_exception *) js_exception_type.tp_alloc(&js_exception_type, 0);
    PyErr_PROPAGATE(self);
    self->exception.Reset(isolate, exception);
    self->message.Reset(isolate, message);

    //PyObject *py_message = py_from_js(exception->ToString(), no_ctx);
    //PyObject *py_message = py_from_js(exception, message->GetIsolate()->GetCurrentContext());
    const std::string msg = extract_exception(exception, message);
    PyObject *py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    PyErr_PROPAGATE(py_message);
    self->base.args = PyTuple_New(1);
    PyErr_PROPAGATE(self->base.args);
    PyTuple_SetItem(self->base.args, 0, py_message);
    return (PyObject *) self;
}

PyObject *js_exception_get_value(js_exception *self, void *args) {
    IN_V8;

    const std::string msg = extract_exception(self->exception.Get(isolate), self->message.Get(isolate));
    PyObject* py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    return py_message;
}


void js_exception_dealloc(js_exception *self) {
    self->exception.Reset();
    self->message.Reset();
    Py_TYPE(self)->tp_free((PyObject *) self);
}

PyTypeObject js_terminated_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int js_terminated_type_init() {
    js_terminated_type.tp_name = "pyv8env.JavaScriptTerminated";
    js_terminated_type.tp_base = (PyTypeObject *) PyExc_Exception;
    js_terminated_type.tp_basicsize = sizeof(PyBaseExceptionObject);
    js_terminated_type.tp_flags = Py_TPFLAGS_DEFAULT;
    js_terminated_type.tp_doc = "";
    return PyType_Ready(&js_terminated_type);
}

void py_throw_js(Local<Value> js_exc, Local<Message> js_message) {
    if (js_exc->IsObject() && js_exc.As<Object>()->InternalFieldCount() == OBJECT_INTERNAL_FIELDS) {
        Local<Object> exc_object = js_exc.As<Object>();
        PyObject *exc_type = (PyObject *) exc_object->GetInternalField(2).As<External>()->Value();
        PyObject *exc_value = (PyObject *) exc_object->GetInternalField(1).As<External>()->Value();
        PyObject *exc_traceback = (PyObject *) exc_object->GetInternalField(3).As<External>()->Value();
        if (exc_type == NULL && exc_traceback == NULL) {
            PyErr_SetObject((PyObject *) Py_TYPE(exc_value), exc_value);
        } else {
            PyErr_Restore(exc_type, exc_value, exc_traceback);
        }
    } else {
        PyObject *exception = js_exception_new(js_exc, js_message);
        if (exception == NULL) {
            return;
        }
        PyErr_SetObject((PyObject *) &js_exception_type, exception);
    }
}

void js_throw_py() {
    Local<Context> context = isolate->GetCurrentContext();
    PyObject *exc_type, *exc_value, *exc_traceback;
    PyErr_Fetch(&exc_type, &exc_value, &exc_traceback);
    PyErr_NormalizeException(&exc_type, &exc_value, &exc_traceback);

    if (!PyObject_HasAttrString(exc_value, "__v8_error__")) {
        PyObject* traceback_module = PyImport_ImportModule("traceback");
        if (traceback_module) {
            PyObject* print_exception_fn = PyObject_GetAttrString(traceback_module, "print_exception");
            if (print_exception_fn) {
                PyObject* result = PyObject_CallFunctionObjArgs(
                    print_exception_fn, exc_type, exc_value, exc_traceback, NULL);
                Py_XDECREF(result);
                Py_DECREF(print_exception_fn);
            }
            Py_DECREF(traceback_module);
        }
    }

    PyObject* exc_value_str = PyObject_Str(exc_value);
    const char* exc_message = PyUnicode_AsUTF8(exc_value_str);
    Py_XDECREF(exc_value_str);
    
    PyObject* exc_cls_name = PyObject_GetAttrString(exc_type, "__name__");
    const char* exc_type_name = PyUnicode_AsUTF8(exc_cls_name);
    Py_XDECREF(exc_value_str);

    Local<Object> exception;
    if (PyObject_TypeCheck(exc_value, &js_exception_type)) {
        exception = ((js_exception *) exc_value)->exception.Get(isolate).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8TypeError") == 0)
    {
        exception = Exception::TypeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8Error") == 0) {
        exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8RangeError") == 0)
    {
        exception = Exception::RangeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8ReferenceError") == 0)
    {
        exception = Exception::ReferenceError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8SyntaxError") == 0)
    {
        exception = Exception::SyntaxError(V8String(isolate, exc_message)).As<Object>();
    }
    else {
        exception = js_from_py(exc_value, context).As<Object>();
        exception->SetInternalField(2, External::New(isolate, exc_type));
        exception->SetInternalField(3, External::New(isolate, exc_traceback));
        exception->Set(context, V8String(isolate, "message"), V8String(isolate, exc_message));
        //v8::Exception::CaptureStackTrace(context, exception);
        
        // 获取Error构造函数
        Local<Value> error_ctor_val;
        if (!context->Global()->Get(context, String::NewFromUtf8Literal(isolate, "Error"))
            .ToLocal(&error_ctor_val)) {
            printf("error in cpp function js_throw_py 1\n");
            fflush(stdout);
        }
        if (!error_ctor_val->IsObject()) {
            printf("error in cpp function js_throw_py 2\n");
            fflush(stdout);
        }
        Local<Object> error_ctor = error_ctor_val.As<Object>();

        // 获取captureStackTrace方法
        Local<Value> capture_stack_trace_val;
        if (!error_ctor->Get(context, V8String(isolate, "captureStackTrace"))
            .ToLocal(&capture_stack_trace_val)) {
            printf("error in cpp function js_throw_py 3\n");
            fflush(stdout);
        }
        if (!capture_stack_trace_val->IsFunction()) {
            printf("error in cpp function js_throw_py 4\n");
            fflush(stdout);
        }
        Local<Function> capture_stack_trace = capture_stack_trace_val.As<Function>();

        // 调用captureStackTrace，参数为目标对象
        Local<Value> args[] = { exception };
        capture_stack_trace->Call(context, Undefined(isolate), 1, args);
    }

    isolate->ThrowException(exception);
}
