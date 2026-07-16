#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "context.h"
#include "script.h"

using namespace v8;


PyTypeObject* script_type = NULL;

static PyType_Slot script_slots[] = {
    {Py_tp_doc, (void*)"v8 Script"},
    {Py_tp_new, (void *)script_new},
    {Py_tp_dealloc, (void *)script_dealloc},
    {0, NULL}};

static PyType_Spec script_spec = {
    "_pyv8.Script",
    sizeof(script_c),
    0,
    Py_TPFLAGS_DEFAULT,
    script_slots};

int 
script_type_init()
{
    script_type = (PyTypeObject *)PyType_FromSpec(&script_spec);
    if (script_type == NULL)
    {
        return -1;
    }
    return 0;
}

PyObject *
construct_script_name(Local<Context> context, Local<Value> js_name, int id)
{
    PyObject *name;
    if (js_name.IsEmpty() || js_name->IsUndefined()) {
        IsolateState* isolate_state = runtime_manager.find_isolate(v8_compat::context_isolate(context));
        name = isolate_state->javascript_name();
        PyErr_PROPAGATE(name);
        Py_INCREF(name);
    } else {
        name = py_from_js(js_name, context);
        PyErr_PROPAGATE(name);
    }
    if (id == 0) {
        return name;
    }
    PyObject *script_name = PyUnicode_FromFormat("%S-%d", name, id);
    Py_XDECREF(name);
    return script_name;
}

PyObject *
script_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    if (!runtime_manager.require_initialized())
        return NULL;
    context_c *py_ctx;
    static const char *keywords[] = {"context", "source", "filename", NULL};
    PyObject *source;
    PyObject *filename = Py_None;
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "OO|O", (char **) keywords, &py_ctx, &source, &filename) < 0) {
        return NULL;
    }
    if (!PyObject_TypeCheck((PyObject*)py_ctx, context_type) || py_ctx->isolate_state == nullptr) {
        PyErr_SetString(PyExc_TypeError, "context must be an active Context instance");
        return NULL;
    }
    IsolateState* isolate_state = py_ctx->isolate_state;
    if (!isolate_state->require_active())
        return NULL;
    IN_ISOLATE(isolate_state);
    IN_CONTEXT(py_ctx->js_context.Get(isolate));
    JS_TRY

    if (!PyUnicode_Check(source))
    {
        PyErr_SetString(PyExc_TypeError, "source must be a string");
        return NULL;
    }
    if (filename != Py_None && !PyUnicode_Check(filename))
    {
        PyErr_SetString(PyExc_TypeError, "filename must be a string or None");
        return NULL;
    }
    MaybeLocal<UnboundScript> maybe_script = script_compile(context, source, filename);
    Local<UnboundScript> script;
    if (!maybe_script.ToLocal(&script))
    {
        PY_PROPAGATE_JS;
        PyErr_SetString(PyExc_RuntimeError, "failed to compile JavaScript");
        return NULL;
    }
    //script_c *self = (script_c *) type->tp_alloc(type, 0);
    script_c *self = (script_c *)PyType_GenericAlloc(type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    self->script.Reset(isolate, script);
    isolate_state->retain_v8_handle();
    if (py_ctx->py_isolate != nullptr) {
        self->py_isolate = py_ctx->py_isolate;
        Py_INCREF(self->py_isolate);
    }
    Py_INCREF(filename);
    self->script_name = filename;
    Py_INCREF(source);
    self->source = source;

    return (PyObject *) self;
}

MaybeLocal<UnboundScript> 
script_compile(Local<Context> context, PyObject *source, PyObject *filename)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    EscapableHandleScope hs(isolate);
    MaybeLocal<UnboundScript> maybe_script;
    // fucking god / c++
    if (filename != Py_None) {
        ScriptOrigin origin(js_from_py(filename, context));
        ScriptCompiler::Source js_source(js_from_py(source, context).As<String>(), origin);
        maybe_script = ScriptCompiler::CompileUnboundScript(isolate, &js_source);
    } else {
        ScriptCompiler::Source js_source(js_from_py(source, context).As<String>());
        maybe_script = ScriptCompiler::CompileUnboundScript(isolate, &js_source);
    }
    Local<UnboundScript> script;
    if (!maybe_script.ToLocal(&script))
        return maybe_script;
    return hs.Escape(script);
}

void 
script_dealloc(script_c *self)
{
    if (!self->script.IsEmpty()) {
        self->script.Reset();
        if (self->isolate_state != nullptr)
            self->isolate_state->release_v8_handle();
    }

    Py_XDECREF(self->script_name);
    Py_XDECREF(self->source);
    Py_XDECREF(self->py_isolate);
    self->isolate_state = nullptr;
    //Py_TYPE(self)->tp_free((PyObject *) self);
    TP_FREE_ABI3(script_type, self);
}
