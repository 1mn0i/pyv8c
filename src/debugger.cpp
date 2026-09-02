#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include "context.h"
#include "debugger.h"
#include <cstring>

using namespace v8;

struct DebuggerState
{
    std::unique_ptr<V8Inspector> inspector;
    std::unique_ptr<V8InspectorSession> session;
};


PyMethodDef debugger_methods[] = {
    {"send", (PyCFunction)debugger_send, METH_O, NULL},
    {"schedule_pause_on_next_statement", (PyCFunction)debugger_schedule_pause, METH_VARARGS | METH_KEYWORDS, NULL},
    {"disconnect", (PyCFunction)debugger_disconnect, METH_NOARGS, NULL},
    {NULL},
};
PyTypeObject *debugger_type = NULL;

static PyType_Slot debugger_slots[] = {
    {Py_tp_dealloc, (void*)debugger_dealloc},
    {Py_tp_new, (void*)debugger_new},
    {Py_tp_init, (void*)debugger_init},
    {Py_tp_methods, debugger_methods},
    {Py_tp_doc, (void*)"v8 inspector"},
    {0, NULL} };

static PyType_Spec debugger_spec = {
    "_pyv8.Debugger",
    sizeof(debugger_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    debugger_slots };

int 
debugger_type_init()
{
    debugger_type = (PyTypeObject *)PyType_FromSpec(&debugger_spec);
    if (debugger_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject * 
debugger_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    //debugger_c* self = (debugger_c*)type->tp_alloc(type, 0);
    debugger_c* self = (debugger_c*)PyType_GenericAlloc(type, 0);

    PyErr_PROPAGATE(self);
    self->context = NULL;
    self->client = NULL; // indicate the object has not been initialized
    self->channel = NULL;
    self->state = NULL;
    self->scripts = NULL;
    return (PyObject*)self;
}

int 
debugger_init(debugger_c* self, PyObject* args, PyObject* kwargs)
{
    if (self->client != NULL || self->state != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Debugger is already initialized");
        return -1;
    }
    PyObject* context_object = NULL;
    static const char* keywords[] = {"context", NULL};
    if (!PyArg_ParseTupleAndKeywords(
            args, kwargs, "O:Debugger", const_cast<char**>(keywords), &context_object)) {
        return -1;
    }
    context_c* context = (context_c*)context_object;
    if (!PyObject_TypeCheck((PyObject*)context, context_type) ||
        context->isolate_state == nullptr || !context->isolate_state->require_active()) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_TypeError, "context must be an active Context instance");
        return -1;
    }
    if (context->has_debugger) {
        PyErr_SetString(PyExc_ValueError, "specified context already has a debugger attached");
        return -1;
    }
    IN_ISOLATE(context->isolate_state);

    Py_INCREF(context);
    self->context = context;
    Local<Context> js_context = context->js_context.Get(isolate);
    context->has_debugger = true;

    std::unique_ptr<DebuggerState> state(new (std::nothrow) DebuggerState());
    std::unique_ptr<V8PyInspectorClient> client(new (std::nothrow) V8PyInspectorClient(self));
    std::unique_ptr<V8PyChannel> channel(new (std::nothrow) V8PyChannel(self));
    if (!state || !client || !channel) {
        context->has_debugger = false;
        Py_CLEAR(self->context);
        PyErr_NoMemory();
        return -1;
    }

    self->client = client.get();
    self->channel = channel.get();
    self->state = state.get();
    state->inspector = V8Inspector::create(isolate, self->client);
    if (!state->inspector) {
        self->client = NULL;
        self->channel = NULL;
        self->state = NULL;
        context->has_debugger = false;
        Py_CLEAR(self->context);
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 inspector");
        return -1;
    }
    state->inspector->contextCreated(V8ContextInfo(js_context, 1, StringView()));
    state->session = state->inspector->connect(
        1, self->channel, StringView(), v8_inspector::V8Inspector::kFullyTrusted);
    if (!state->session) {
        self->client = NULL;
        self->channel = NULL;
        self->state = NULL;
        context->has_debugger = false;
        Py_CLEAR(self->context);
        PyErr_SetString(PyExc_RuntimeError, "failed to create V8 inspector session");
        return -1;
    }
    client.release();
    channel.release();
    state.release();

    return 0;
}

void 
call_self(debugger_c* self, const char* method)
{
    PyObject* debugger = (PyObject*)self;
    if (PyObject_HasAttrString(debugger, method)) {
        if (PyObject_CallMethod(debugger, (char*)method, NULL) == NULL) {
            PyErr_WriteUnraisable(debugger);
        }
    }
}

void 
V8PyChannel::handle_message(const std::unique_ptr<StringBuffer>& message)
{
    if (message == nullptr)
        return;
    const StringView& stringview = message->string();
    PyObject* string;
    if (stringview.is8Bit())
        string = PyUnicode_DecodeLatin1((const char*)stringview.characters8(), stringview.length(), NULL);
    else 
        string = PyUnicode_DecodeUTF16((const char*)stringview.characters16(), stringview.length() * 2, NULL, NULL);

    PyObject* self = (PyObject*)debugger_;
    if (PyObject_HasAttrString(self, "handle")) {
        PyObject* result = string == nullptr ? NULL :
            PyObject_CallMethod(self, (char*)"handle", (char*)"O", string);
        if (result == NULL) {
            PyErr_WriteUnraisable(self);
        }
        else {
            Py_DECREF(result);
        }
    }
    Py_XDECREF(string);
}

void 
V8PyInspectorClient::runMessageLoopOnPause(int /*context_group_id*/)
{
    call_self(debugger_, "run_loop");
}

void
V8PyInspectorClient::runIfWaitingForDebugger(int /*context_group_id*/)
{
    // Python execution must wait until V8 finishes dispatching the CDP message.
    run_if_waiting_requested_ = true;
}

bool
V8PyInspectorClient::consumeRunIfWaitingForDebuggerRequest()
{
    bool requested = run_if_waiting_requested_;
    run_if_waiting_requested_ = false;
    return requested;
}

void 
V8PyInspectorClient::quitMessageLoopOnPause()
{
    call_self(debugger_, "quit_loop");
}

PyObject *
debugger_send(debugger_c* self, PyObject* message)
{
    if (self->client == NULL || self->state == NULL || self->state->session == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "__init__ has not yet been called");
        return NULL;
    }

    if (self->context == nullptr || self->context->isolate_state == nullptr ||
        !self->context->isolate_state->require_active()) {
        PyErr_SetString(PyExc_RuntimeError, "Debugger context is not active");
        return NULL;
    }
    IN_ISOLATE(self->context->isolate_state);
    IN_CONTEXT(self->context->js_context.Get(isolate));

    //const uint8_t* command_bytes = (const uint8_t*)PyBytes_AS_STRING(message);
    //int length = PyBytes_GET_SIZE(message);

    char* command_bytes_str;
    Py_ssize_t length;
    if (PyBytes_AsStringAndSize(message, &command_bytes_str, &length) < 0)
    {
        return NULL;
    }
    const uint8_t* command_bytes = (const uint8_t*)command_bytes_str;

    std::unique_ptr<StringBuffer> stringview = StringBuffer::create(StringView(command_bytes, length));

    if (stringview.get() == NULL) return NULL;
    self->state->session->dispatchProtocolMessage(stringview.get()->string());

    if (self->client->consumeRunIfWaitingForDebuggerRequest()) {
        std::string_view reason = "run script";
        StringView inspector_reason(
            reinterpret_cast<const uint8_t*>(reason.data()), reason.size());
        self->state->session->schedulePauseOnNextStatement(
            inspector_reason, inspector_reason);
        if (PyObject_HasAttrString((PyObject*)self, "on_run_if_waiting_for_debugger"))
            call_self(self, "on_run_if_waiting_for_debugger");
        else
            call_self(self, "run_js");
    }
    PYV8_RETURN_TRUE;
}

PyObject *
debugger_schedule_pause(debugger_c* self, PyObject* args, PyObject* kwargs)
{
    const char* reason = "pause on next statement";
    static const char* keywords[] = {"reason", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|s:schedule_pause_on_next_statement",
                                     const_cast<char**>(keywords), &reason))
        return NULL;
    if (self->state == NULL || self->state->session == nullptr || self->context == nullptr ||
        self->context->isolate_state == nullptr || !self->context->isolate_state->require_active()) {
        PyErr_SetString(PyExc_RuntimeError, "Debugger is not active");
        return NULL;
    }
    IN_ISOLATE(self->context->isolate_state);
    IN_CONTEXT(self->context->js_context.Get(isolate));
    StringView inspector_reason(reinterpret_cast<const uint8_t*>(reason), strlen(reason));
    self->state->session->schedulePauseOnNextStatement(inspector_reason, inspector_reason);
    PYV8_RETURN_NONE;
}

PyObject *
debugger_disconnect(debugger_c* self, PyObject* ignored)
{
    (void)ignored;
    if (self->state == NULL)
        PYV8_RETURN_NONE;
    IsolateState* isolate_state = self->context == nullptr ? nullptr : self->context->isolate_state;
    if (isolate_state != nullptr && isolate_state->require_active()) {
        IN_ISOLATE(isolate_state);
        if (self->state->session != nullptr && self->state->inspector != nullptr && self->context != nullptr &&
            !self->context->js_context.IsEmpty())
            self->state->inspector->contextDestroyed(self->context->js_context.Get(isolate));
        self->state->session.reset();
    } else {
        self->state->session.reset();
    }
    PYV8_RETURN_NONE;
}

void 
debugger_dealloc(debugger_c* self)
{
    PyObject* disconnected = debugger_disconnect(self, NULL);
    Py_XDECREF(disconnected);
    IsolateState* isolate_state = self->context == nullptr ? nullptr : self->context->isolate_state;
    if (isolate_state != nullptr && isolate_state->is_active()) {
        IN_ISOLATE(isolate_state);
        delete self->state;
        self->state = NULL;
    }
    else {
        delete self->state;
        self->state = NULL;
    }
    delete self->client;
    delete self->channel;
    if (self->context != nullptr)
        self->context->has_debugger = false;
    Py_XDECREF(self->context);
    //Py_TYPE(self)->tp_free((PyObject*)self);
    TP_FREE_ABI3(debugger_type, self);
}
