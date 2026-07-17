#include <Python.h>
#include "pyv8.h"
#include <v8.h>
#include "context.h"
#include "debugger.h"

using namespace v8;


PyMethodDef debugger_methods[] = {
    {"send", (PyCFunction)debugger_send, METH_O, NULL},
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
    self->client = NULL; // indicate the object has not been initialized
    return (PyObject*)self;
}

int 
debugger_init(debugger_c* self, PyObject* args, PyObject* kwargs)
{
    context_c* context;
    if (PyArg_ParseTuple(args, "O", &context) < 0) {
        return -1;
    }
    if (!PyObject_TypeCheck((PyObject*)context, context_type) ||
        context->isolate_state == nullptr || !context->isolate_state->require_active()) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_TypeError, "context must be an active Context instance");
        return -1;
    }
    IN_ISOLATE(context->isolate_state);

    Py_INCREF(context);
    self->context = context;
    Local<Context> js_context = context->js_context.Get(isolate);

    if (context->has_debugger) {
        PyErr_SetString(PyExc_ValueError, "specified context already has a debugger attached");
        return -1;
    }
    context->has_debugger = true;

    self->client = new V8PyInspectorClient(self);
    self->inspector = V8Inspector::create(isolate, self->client);
    self->inspector->contextCreated(V8ContextInfo(js_context, 1, StringView()));
    self->channel = new V8PyChannel(self);
    self->session = self->inspector->connect(1, self->channel, StringView(), v8_inspector::V8Inspector::kFullyTrusted);

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
    const StringView& stringview = message->string();
    PyObject* string;
    if (stringview.is8Bit())
        string = PyUnicode_DecodeLatin1((const char*)stringview.characters8(), stringview.length(), NULL);
    else 
        string = PyUnicode_DecodeUTF16((const char*)stringview.characters16(), stringview.length() * 2, NULL, NULL);

    PyObject* self = (PyObject*)debugger_;
    if (PyObject_HasAttrString(self, "handle")) {
        if (PyObject_CallMethod(self, (char*)"handle", (char*)"O", string) == NULL) {
            PyErr_WriteUnraisable(self);
        }
    }
}

void 
V8PyInspectorClient::runMessageLoopOnPause(int /*context_group_id*/)
{
    call_self(debugger_, "run_loop");
}

void 
V8PyInspectorClient::quitMessageLoopOnPause()
{
    call_self(debugger_, "quit_loop");
}

PyObject * 
debugger_send(debugger_c* self, PyObject* message)
{
    if (self->client == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "__init__ has not yet been called");
        return NULL;
    }

    if (self->context == nullptr || self->context->isolate_state == nullptr ||
        !self->context->isolate_state->require_active())
        return NULL;
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
    self->session.get()->dispatchProtocolMessage(stringview.get()->string());

    std::string msg((char*)command_bytes);
    if (strstr(msg.c_str(), "Runtime.runIfWaitingForDebugger") != NULL) {
        std::string_view ss = "run script";
        self->session.get()->schedulePauseOnNextStatement({ (uint8_t*)ss.data(), ss.length() }, { (uint8_t*)ss.data(), ss.length() });
        call_self(self, "run_js");
    }
    Py_RETURN_TRUE;
}

void 
debugger_dealloc(debugger_c* self)
{
    IsolateState* isolate_state = self->context == nullptr ? nullptr : self->context->isolate_state;
    if (isolate_state != nullptr && isolate_state->is_active()) {
        IN_ISOLATE(isolate_state);
        self->session.reset();
        self->inspector.reset();
    }
    delete self->client;
    delete self->channel;
    if (self->context != nullptr)
        self->context->has_debugger = false;
    Py_XDECREF(self->context);
    //Py_TYPE(self)->tp_free((PyObject*)self);
    TP_FREE_ABI3(debugger_type, self);
}
