#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include <new>
#include <sstream>

#include "handle_resource.h"
#include "convert.h"

using namespace v8;

// The parts a page reports, not the prose they are rendered in.
//
// The state is an attribute because the limited API gives a subclass of BaseException no
// fields of its own, and this type has to be one.  Every getter reaches it the same way,
// and every one of them says the same thing when it is not there.
static JsExceptionState* state_of(PyObject* self)
{
    JsExceptionState* state = js_exception_get_state(self);
    if (state == nullptr) {
        PyErr_Clear();
        PyErr_SetString(PyExc_RuntimeError, "JSException was not created from a V8 exception");
    }
    return state;
}

static PyObject* js_exception_filename(PyObject* self, void*) {
    JsExceptionState* state = state_of(self);
    if (state == nullptr) return nullptr;
    ExecutionScope execution((context_c*)state->py_context, self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Message> message = state->message.Get(isolate);
    if (message.IsEmpty()) return PyUnicode_FromString("");
    Local<Value> name = message->GetScriptResourceName();
    if (name.IsEmpty() || name->IsUndefined() || name->IsNull())
        return PyUnicode_FromString("");
    v8::String::Utf8Value text(isolate, name);
    if (!*text) return PyUnicode_FromString("");
    return PyUnicode_DecodeUTF8(*text, text.length(), nullptr);
}

static PyObject* js_exception_line(PyObject* self, void*) {
    JsExceptionState* state = state_of(self);
    if (state == nullptr) return nullptr;
    ExecutionScope execution((context_c*)state->py_context, self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Message> message = state->message.Get(isolate);
    int line = message.IsEmpty()
        ? 0
        : message->GetLineNumber(isolate->GetCurrentContext()).FromMaybe(0);
    return PyLong_FromLong(line);
}

static PyObject* js_exception_column(PyObject* self, void*) {
    JsExceptionState* state = state_of(self);
    if (state == nullptr) return nullptr;
    ExecutionScope execution((context_c*)state->py_context, self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    Local<Message> message = state->message.Get(isolate);
    return PyLong_FromLong(message.IsEmpty() ? 0 : message->GetStartColumn());
}

static PyObject* js_exception_original(PyObject* self, void*) {
    JsExceptionState* state = state_of(self);
    if (!state) return nullptr;
    auto* owner = (context_c*)state->py_context;
    ExecutionScope execution(owner, self);
    if (!execution) return nullptr;
    auto* isolate = execution.isolate();
    auto context = execution.context();
    return py_from_js(state->exception.Get(isolate), context);
}

PyGetSetDef js_exception_getsets[] = {
    {"original", (getter)PythonBoundary<js_exception_original>::call, NULL, NULL},
    {"filename", (getter)PythonBoundary<js_exception_filename>::call, NULL, NULL},
    {"line", (getter)PythonBoundary<js_exception_line>::call, NULL, NULL},
    {"column", (getter)PythonBoundary<js_exception_column>::call, NULL, NULL},
    {NULL},
};

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot js_exception_slots[] = {
    {Py_tp_getset, js_exception_getsets},
    {Py_tp_doc, (void*)"v8 Exception"},
    {0, NULL} };

static PyType_Spec js_exception_spec = {
    "pyv8.JSException",
    0,
    0,
    Py_TPFLAGS_DEFAULT,
    js_exception_slots };

PyTypeObject* js_exception_type = NULL;

namespace
{
constexpr const char* kExceptionStateAttribute = "_pyv8_exception_state";
PyTypeObject* exception_state_type = nullptr;

int clear_exception_state(JsExceptionState* state)
{
    std::optional<IsolateLockScope> lock;
    if (state->isolate_state) lock.emplace(state->isolate_state);
    context_unregister_handle((context_c*)state->py_context, (PyObject*)state);
    state->exception.Reset();
    state->message.Reset();
    state->isolate_state = nullptr;
    Py_CLEAR(state->py_context);
    Py_CLEAR(state->py_isolate);
    return 0;
}

int traverse_exception_state(JsExceptionState* state, visitproc visit, void* arg) {
    Py_VISIT(state->py_context);
    Py_VISIT(state->py_isolate);
    return 0;
}
void release_exception_state(JsExceptionState* state) {
    PyObject_GC_UnTrack(state);
    clear_exception_state(state);
    TP_FREE_ABI3(exception_state_type, state);
}
}

JsExceptionState*
js_exception_get_state(PyObject* exception)
{
    PyObject* capsule = PyObject_GetAttrString(exception, kExceptionStateAttribute);
    if (capsule == nullptr)
        return nullptr;
    if (!PyObject_TypeCheck(capsule, exception_state_type)) {
        Py_DECREF(capsule);
        PyErr_SetString(PyExc_TypeError, "invalid JavaScript exception state");
        return nullptr;
    }
    JsExceptionState* state = (JsExceptionState*)capsule;
    Py_DECREF((PyObject*)capsule);
    return state;
}

int
js_exception_type_init()
{    // Create the type using PyType_FromSpec for ABI3 compatibility
    PyType_Slot state_slots[] = {
        {Py_tp_dealloc, (void*)PythonBoundary<release_exception_state>::call},
        {Py_tp_clear, (void*)PythonBoundary<clear_exception_state>::call},
        {Py_tp_traverse, (void*)PythonBoundary<traverse_exception_state>::call}, {0, nullptr}};
    PyType_Spec state_spec = {"pyv8.ExceptionState", sizeof(JsExceptionState), 0,
                             Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC, state_slots};
    exception_state_type = (PyTypeObject*)PyType_FromSpec(&state_spec);
    if (!exception_state_type) return -1;
    PyObject* bases = PyTuple_New(1);
    if (bases == NULL)
    {
        return -1;
    }
    Py_INCREF((PyObject*)PyExc_BaseException);
    PyTuple_SetItem(bases, 0, (PyObject*)PyExc_BaseException);

    js_exception_type = (PyTypeObject*)PyType_FromSpecWithBases(&js_exception_spec, bases);
    Py_DECREF((PyObject*)bases);

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
    js_terminated_type = (PyTypeObject*)PyErr_NewException("pyv8.JavaScriptTerminated", PyExc_BaseException, nullptr);
    if (js_terminated_type == NULL)
    {
        return -1;
    }
    Py_INCREF((PyObject*)js_terminated_type);
    return 0;
}


// The one rendering of a JavaScript exception as text: what the exception says, and --
// when V8 reported a message -- where it said it, the line it appears on and the stack
// beneath it.  It is what ``str()`` answers, and the one place that text is made.
std::string
extract_exception(Isolate* isolate, Local<Value> exception, Local<Message> message)
{
    assert(isolate->InContext());

    std::ostringstream oss;

    {
        // Reading the value runs the page's own ``toString``, which may throw or say
        // nothing.  Either way the failure is answered here and consumed, so it is not
        // left pending for whatever JavaScript runs next.
        v8::TryCatch refusal(isolate);
        v8::String::Utf8Value msg(isolate, exception);
        oss << (*msg ? std::string(*msg, msg.length()) : std::string("<no message>"));
    }

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

            oss << "\n";
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
    Py_DECREF((PyObject*)py_message);
    if (self == NULL)
        return NULL;

    JsExceptionState* state = (JsExceptionState*)PyType_GenericAlloc(exception_state_type, 0);
    if (state == nullptr) {
        Py_DECREF((PyObject*)self);
        PyErr_NoMemory();
        return NULL;
    }
    state->isolate_state = isolate_state;
    state->exception.Set(isolate_state, isolate, exception);
    state->message.Set(isolate_state, isolate, message);
    Local<Context> context = isolate->GetCurrentContext();
    if (!context.IsEmpty()) {
        Local<Value> context_data = context->GetEmbedderData(CONTEXT_OBJECT_SLOT);
        if (context_data->IsExternal()) {
            state->py_context = (PyObject*)context_from_v8(context);
            Py_INCREF(state->py_context);
            context_c* py_context = (context_c*)state->py_context;
            context_register_handle(py_context, (PyObject*)state, (int (*)(PyObject*))clear_exception_state);
            if (py_context->py_isolate != nullptr) {
                state->py_isolate = py_context->py_isolate;
                Py_INCREF(state->py_isolate);
            }
        }
    }
    PyObject* capsule = (PyObject*)state;
    if (PyObject_SetAttrString(self, kExceptionStateAttribute, capsule) < 0) {
        Py_DECREF((PyObject*)capsule);
        Py_DECREF((PyObject*)self);
        return NULL;
    }
    Py_DECREF((PyObject*)capsule);
    return self;
}

PyObject*
pyexception_message(PyObject* value)
{
    PyObject* args = value == nullptr ? nullptr : PyObject_GetAttrString(value, "args");
    if (args != nullptr && PyTuple_Check(args) && PyTuple_Size(args) > 0)
    {
        PyObject* first = PyTuple_GetItem(args, 0);
        if (first != nullptr && PyUnicode_Check(first))
        {
            Py_INCREF(first);
            Py_DECREF((PyObject*)args);
            return first;
        }
    }
    Py_XDECREF((PyObject*)args);
    if (PyErr_Occurred())
        PyErr_Clear();
    return value == nullptr ? nullptr : PyObject_Str(value);
}
