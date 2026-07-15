#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <sstream>

#include "script.h"
#include "pyclass.h"
#include "convert.h"

PyGetSetDef js_exception_getsets[] = {
    {"value", (getter)js_exception_get_value, NULL, NULL},
    {NULL},
};

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot js_exception_slots[] = {
    //{Py_tp_base, (void*)PyExc_Exception},
    {Py_tp_dealloc, (void*)js_exception_dealloc},
    {Py_tp_getset, js_exception_getsets},
    {Py_tp_doc, "v8 Exception"},
    {0, NULL} };

static PyType_Spec js_exception_spec = {
    "_pyv8.JSException",
    sizeof(js_exception),
    0,
    Py_TPFLAGS_DEFAULT,
    js_exception_slots };

PyTypeObject* js_exception_type = NULL;

int 
js_exception_type_init()
{    // Create the type using PyType_FromSpec for ABI3 compatibility
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    Py_INCREF(PyExc_BaseException);
    PyTuple_SetItem(bases, 0, (PyObject*)PyExc_BaseException);

    js_exception_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_exception_spec, bases);
    Py_DECREF(bases);

    //js_exception_type = (PyTypeObject*)PyType_FromSpec(&js_exception_spec);
    if (js_exception_type == NULL)
    {
        return -1;
    }
    return 0;
}


PyTypeObject* js_terminated_type = NULL;

int 
js_terminated_type_init()
{
    js_terminated_type = (PyTypeObject*)PyErr_NewException("_pyv8env.JavaScriptTerminated", PyExc_BaseException, nullptr);
    if (js_terminated_type == NULL)
    {
        return -1;
    }
    Py_INCREF(js_terminated_type);
    return 0;
}


const std::string
extract_exception(Isolate* isolate, Local<Value> exception, Local<Message> message)
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

        int line_number = message->GetLineNumber(isolate->GetCurrentContext()).FromMaybe(0);
        oss << " @ " << line_number << " : " << message->GetStartColumn() << " ) ";

        Local<String> source_line;
        if (message->GetSourceLine(isolate->GetCurrentContext()).ToLocal(&source_line))
        {
            v8::String::Utf8Value line(isolate, source_line);

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

PyObject * 
js_exception_new(Local<Value> exception, Local<Message> message)
{
    IsolateState* isolate_state = current_isolate_state();
    if (isolate_state == nullptr || !isolate_state->require_active())
        return NULL;
    Isolate* isolate = isolate_state->isolate();
    HandleScope hs(isolate);
    Local<Context> no_ctx;

    js_exception* self = (js_exception*)PyType_GenericAlloc(js_exception_type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    self->exception.Reset(isolate, exception);
    self->message.Reset(isolate, message);
    isolate_state->retain_v8_handle();
    Local<Context> context = isolate->GetCurrentContext();
    if (!context.IsEmpty()) {
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            self->py_context = (PyObject*)context_data.As<External>()->Value();
            Py_INCREF(self->py_context);
            context_c* py_context = (context_c*)self->py_context;
            if (py_context->py_isolate != nullptr) {
                self->py_isolate = py_context->py_isolate;
                Py_INCREF(self->py_isolate);
            }
        }
    }
    const std::string msg = extract_exception(isolate, exception, message);
    PyObject* py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    if (py_message == NULL) {
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    self->args = PyTuple_New(1);
    if (self->args == NULL) {
        Py_DECREF(py_message);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    PyTuple_SetItem(self->args, 0, py_message);
    return (PyObject*)self;
}

PyObject * 
js_exception_get_value(js_exception* self, void* args)
{
    if (self->isolate_state == nullptr || !self->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(self->isolate_state);

    const std::string msg = extract_exception(
        isolate, self->exception.Get(isolate), self->message.Get(isolate));
    PyObject* py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    return py_message;
}


void 
js_exception_dealloc(js_exception* self)
{
    if (!self->exception.IsEmpty() || !self->message.IsEmpty()) {
        self->exception.Reset();
        self->message.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }
    Py_XDECREF(self->py_context);
    Py_XDECREF(self->py_isolate);
    self->isolate_state = nullptr;
    Py_XDECREF(self->dict);
    Py_XDECREF(self->args);
    Py_XDECREF(self->notes);
    Py_XDECREF(self->traceback);
    Py_XDECREF(self->context);
    Py_XDECREF(self->cause);
    //Py_TYPE(self)->tp_free((PyObject*)self);
    TP_FREE_ABI3(js_exception_type, self);
}


void 
py_throw_js(Local<Value> js_exc, Local<Message> js_message)
{
    IsolateState* isolate_state = current_isolate_state();
    if (isolate_state == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "no active V8 isolate");
        return;
    }
    bool is_python_object = false;
    if (js_exc->IsObject() &&
        js_exc.As<Object>()->InternalFieldCount() == OBJECT_INTERNAL_FIELDS) {
        Local<Data> magic = js_exc.As<Object>()->GetInternalField(PYTHON_MAGIC_FIELD);
        is_python_object =
            magic == isolate_state->v8_string(V8StringId::PythonObjectMagic);
    }
    if (is_python_object) {
        Local<Object> exc_object = js_exc.As<Object>();
        PyObject* exc_type = (PyObject*)exc_object->GetInternalField(PYTHON_EXCEPTION_TYPE_FIELD).As<External>()->Value();
        PyObject* exc_value = (PyObject*)exc_object->GetInternalField(PYTHON_OBJECT_FIELD).As<External>()->Value();
        PyObject* exc_traceback = (PyObject*)exc_object->GetInternalField(PYTHON_TRACEBACK_FIELD).As<External>()->Value();
        if (exc_type == NULL && exc_traceback == NULL) {
            PyErr_SetObject((PyObject*)Py_TYPE(exc_value), exc_value);
        }
        else {
            Py_XINCREF(exc_type);
            Py_INCREF(exc_value);
            Py_XINCREF(exc_traceback);
            PyErr_Restore(exc_type, exc_value, exc_traceback);
        }
    }
    else {
        PyObject* exception = js_exception_new(js_exc, js_message);
        if (exception == NULL) {
            return;
        }
        PyErr_SetObject((PyObject*)js_exception_type, exception);
        Py_DECREF(exception);
    }
}

void 
js_throw_py()
{
    IsolateState* isolate_state = current_isolate_state();
    if (isolate_state == nullptr || !isolate_state->is_active()) {
        PyErr_SetString(PyExc_RuntimeError, "no active V8 isolate");
        return;
    }
    Isolate* isolate = isolate_state->isolate();
    Local<Context> context = isolate->GetCurrentContext();
    PyObject* exc_type, * exc_value, * exc_traceback;
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
    const char* exc_message = "Python exception";
    if (exc_value_str != NULL) {
        const char* message = PyUnicode_AsUTF8_abi3(exc_value_str);
        if (message != NULL) {
            exc_message = message;
        }
        else {
            PyErr_Clear();
        }
    }
    else {
        PyErr_Clear();
    }

    PyObject* exc_cls_name = PyObject_GetAttrString(exc_type, "__name__");
    const char* exc_type_name = "";
    if (exc_cls_name != NULL) {
        const char* type_name = PyUnicode_AsUTF8_abi3(exc_cls_name);
        if (type_name != NULL) {
            exc_type_name = type_name;
        }
        else {
            PyErr_Clear();
        }
    }
    else {
        PyErr_Clear();
    }

    Local<Object> exception;
    if (PyObject_TypeCheck(exc_value, js_exception_type)) {
        js_exception* py_exception = (js_exception*)exc_value;
        if (py_exception->isolate_state != isolate_state) {
            exception = Exception::Error(
                V8String(isolate, "JavaScript value belongs to another isolate")).As<Object>();
        }
        else {
            exception = py_exception->exception.Get(isolate).As<Object>();
        }
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
        Local<Value> js_exception_value = js_from_py(exc_value, context);
        if (js_exception_value.IsEmpty() || !js_exception_value->IsObject()) {
            PyErr_Clear();
            exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
        }
        else {
            exception = js_exception_value.As<Object>();
            if (!isolate_state->attach_exception_state(exception, exc_type, exc_traceback)) {
                PyErr_Clear();
                exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
            }
            exception->Set(
                context,
                isolate_state->v8_string(V8StringId::Message),
                V8String(isolate, exc_message));

            Local<Value> error_ctor_val;
            Local<Value> capture_stack_trace_val;
            if (context->Global()->Get(context, String::NewFromUtf8Literal(isolate, "Error")).ToLocal(&error_ctor_val) &&
                error_ctor_val->IsObject() &&
                error_ctor_val.As<Object>()->Get(context, V8String(isolate, "captureStackTrace")).ToLocal(&capture_stack_trace_val) &&
                capture_stack_trace_val->IsFunction()) {
                Local<Function> capture_stack_trace = capture_stack_trace_val.As<Function>();
                Local<Value> args[] = { exception };
                capture_stack_trace->Call(context, Undefined(isolate), 1, args);
            }
        }
    }

    Py_XDECREF(exc_cls_name);
    Py_XDECREF(exc_value_str);
    Py_XDECREF(exc_type);
    Py_XDECREF(exc_value);
    Py_XDECREF(exc_traceback);

    isolate->ThrowException(exception);
}
