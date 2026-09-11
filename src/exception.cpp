#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <new>
#include <sstream>

#include "script.h"
#include "pyclass.h"
#include "convert.h"

using namespace v8;

static PyObject* js_exception_original(PyObject* self, void*) {
    JsExceptionState* state = js_exception_get_state(self);
    if (!state || !state->isolate_state->require_active()) return nullptr;
    IN_ISOLATE(state->isolate_state);
    auto* owner = (context_c*)state->py_context;
    if (owner == nullptr || owner->js_context.IsEmpty()) {
        PyErr_SetString(PyExc_RuntimeError, "JavaScript exception context is no longer active");
        return nullptr;
    }
    IN_CONTEXT(owner->js_context.Get(isolate));
    return py_argument_from_js(state->exception.Get(isolate), context);
}

PyGetSetDef js_exception_getsets[] = {
    {"original", (getter)js_exception_original, NULL, NULL},
    {"value", (getter)js_exception_get_value, NULL, NULL},
    {NULL},
};

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot js_exception_slots[] = {
    {Py_tp_getset, js_exception_getsets},
    {Py_tp_doc, (void*)"v8 Exception"},
    {0, NULL} };

static PyType_Spec js_exception_spec = {
    "_pyv8.JSException",
    0,
    0,
    Py_TPFLAGS_DEFAULT,
    js_exception_slots };

PyTypeObject* js_exception_type = NULL;

namespace
{
constexpr const char* kExceptionStateAttribute = "_pyv8_exception_state";
constexpr const char* kExceptionStateCapsule = "pyv8.JSExceptionState";

void release_exception_state(JsExceptionState* state)
{
    if (state == nullptr)
        return;
    state->exception.Reset();
    state->message.Reset();
    if (state->retained_handle && state->isolate_state != nullptr)
        state->isolate_state->release_v8_handle();
    Py_XDECREF(state->py_context);
    Py_XDECREF(state->py_isolate);
    delete state;
}

void exception_state_capsule_destructor(PyObject* capsule)
{
    void* pointer = PyCapsule_GetPointer(capsule, kExceptionStateCapsule);
    if (pointer == nullptr) {
        PyErr_Clear();
        return;
    }
    release_exception_state(static_cast<JsExceptionState*>(pointer));
}
}

JsExceptionState*
js_exception_get_state(PyObject* exception)
{
    PyObject* capsule = PyObject_GetAttrString(exception, kExceptionStateAttribute);
    if (capsule == nullptr)
        return nullptr;
    JsExceptionState* state = static_cast<JsExceptionState*>(
        PyCapsule_GetPointer(capsule, kExceptionStateCapsule));
    Py_DECREF(capsule);
    return state;
}

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
        for (int i = 0; !stack_trace.IsEmpty() && i < stack_trace->GetFrameCount(); i++)
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

    const std::string msg = extract_exception(isolate, exception, message);
    PyObject* py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    if (py_message == NULL)
        return NULL;
    PyObject* self = PyObject_CallFunctionObjArgs(
        (PyObject*)js_exception_type, py_message, NULL);
    Py_DECREF(py_message);
    if (self == NULL)
        return NULL;

    JsExceptionState* state = new (std::nothrow) JsExceptionState{
        isolate_state, {}, {}, nullptr, nullptr, false};
    if (state == nullptr) {
        Py_DECREF(self);
        PyErr_NoMemory();
        return NULL;
    }
    state->exception.Reset(isolate, exception);
    state->message.Reset(isolate, message);
    isolate_state->retain_v8_handle();
    state->retained_handle = true;
    Local<Context> context = isolate->GetCurrentContext();
    if (!context.IsEmpty()) {
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            state->py_context = (PyObject*)v8_compat::external_value(context_data);
            Py_INCREF(state->py_context);
            context_c* py_context = (context_c*)state->py_context;
            if (py_context->py_isolate != nullptr) {
                state->py_isolate = py_context->py_isolate;
                Py_INCREF(state->py_isolate);
            }
        }
    }
    PyObject* capsule = PyCapsule_New(
        state, kExceptionStateCapsule, exception_state_capsule_destructor);
    if (capsule == NULL) {
        release_exception_state(state);
        Py_DECREF(self);
        return NULL;
    }
    if (PyObject_SetAttrString(self, kExceptionStateAttribute, capsule) < 0) {
        Py_DECREF(capsule);
        Py_DECREF(self);
        return NULL;
    }
    Py_DECREF(capsule);
    return self;
}

PyObject * 
js_exception_get_value(PyObject* self, void* args)
{
    JsExceptionState* state = js_exception_get_state(self);
    if (state == nullptr) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError)) {
            PyErr_Clear();
            PyErr_SetString(PyExc_RuntimeError,
                            "JSException was not created from a V8 exception");
        }
        return NULL;
    }
    if (state->isolate_state == nullptr || !state->isolate_state->require_active())
        return NULL;
    IN_ISOLATE(state->isolate_state);

    const std::string msg = extract_exception(
        isolate, state->exception.Get(isolate), state->message.Get(isolate));
    PyObject* py_message = PyUnicode_DecodeUTF8(msg.c_str(), msg.size(), NULL);
    return py_message;
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
        PyObject* exc_type = (PyObject*)v8_compat::external_value(exc_object->GetInternalField(PYTHON_EXCEPTION_TYPE_FIELD));
        PyObject* exc_value = (PyObject*)v8_compat::external_value(exc_object->GetInternalField(PYTHON_OBJECT_FIELD));
        PyObject* exc_traceback = (PyObject*)v8_compat::external_value(exc_object->GetInternalField(PYTHON_TRACEBACK_FIELD));
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

    if (exc_value && PyObject_TypeCheck(exc_value, js_exception_type)) {
        JsExceptionState* state = js_exception_get_state(exc_value);
        if (state && state->isolate_state == isolate_state) {
            isolate->ThrowException(state->exception.Get(isolate));
            Py_XDECREF(exc_type);
            Py_XDECREF(exc_value);
            Py_XDECREF(exc_traceback);
            return;
        }
        PyErr_Clear();
    }

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
    PyObject* js_error_name = exc_value == NULL
        ? NULL : PyObject_GetAttrString(exc_value, "__v8_error_name__");
    bool has_explicit_js_error_name = false;
    if (js_error_name != NULL && PyUnicode_Check(js_error_name)) {
        Py_XDECREF(exc_cls_name);
        exc_cls_name = js_error_name;
        js_error_name = NULL;
        has_explicit_js_error_name = true;
    }
    else if (js_error_name == NULL && PyErr_Occurred()) {
        PyErr_Clear();
    }
    Py_XDECREF(js_error_name);
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
        JsExceptionState* state = js_exception_get_state(exc_value);
        if (state == nullptr || state->isolate_state != isolate_state) {
            PyErr_Clear();
            exception = Exception::Error(
                V8String(isolate, "JavaScript value belongs to another isolate")).As<Object>();
        }
        else {
            exception = state->exception.Get(isolate).As<Object>();
        }
    }
    else if (strcmp(exc_type_name, "V8TypeError") == 0 ||
             (has_explicit_js_error_name && strcmp(exc_type_name, "TypeError") == 0))
    {
        exception = Exception::TypeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8Error") == 0 ||
             (has_explicit_js_error_name && strcmp(exc_type_name, "Error") == 0)) {
        exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8RangeError") == 0 ||
             (has_explicit_js_error_name && strcmp(exc_type_name, "RangeError") == 0))
    {
        exception = Exception::RangeError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8ReferenceError") == 0 ||
             (has_explicit_js_error_name && strcmp(exc_type_name, "ReferenceError") == 0))
    {
        exception = Exception::ReferenceError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "V8SyntaxError") == 0 ||
             (has_explicit_js_error_name && strcmp(exc_type_name, "SyntaxError") == 0))
    {
        exception = Exception::SyntaxError(V8String(isolate, exc_message)).As<Object>();
    }
    else if (strcmp(exc_type_name, "DOMException") == 0)
    {
        Local<Value> ctor_value;
        if (context->Global()->Get(context, V8String(isolate, "DOMException")).ToLocal(&ctor_value) &&
            ctor_value->IsFunction()) {
            Local<Function> ctor = ctor_value.As<Function>();
            PyObject* name_obj = PyObject_GetAttrString(exc_value, "_name");
            const char* name = "Error";
            if (name_obj != nullptr && PyUnicode_Check(name_obj)) {
                const char* value = PyUnicode_AsUTF8_abi3(name_obj);
                if (value != nullptr) name = value;
            }
            Local<Value> args[] = { V8String(isolate, exc_message), V8String(isolate, name) };
            Local<Object> created;
            if (ctor->NewInstance(context, 2, args).ToLocal(&created)) {
                exception = created;
                PyObject* code_obj = PyObject_GetAttrString(exc_value, "_code");
                if (code_obj != nullptr) {
                    int code = (int)PyLong_AsLong(code_obj);
                    PyErr_Clear();
                    exception->Set(context, V8String(isolate, "code"), Integer::New(isolate, code));
                    Py_DECREF(code_obj);
                }
            }
            Py_XDECREF(name_obj);
        }
        if (exception.IsEmpty())
            exception = Exception::Error(V8String(isolate, exc_message)).As<Object>();
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
