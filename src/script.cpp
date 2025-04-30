#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#include "convert.h"
#include "context.h"
#include "script.h"

using namespace v8;


PyObject* javascript;

PyTypeObject* script_type = NULL;

static PyType_Slot script_slots[] = {
    {Py_tp_doc, "v8 Script"},
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
    javascript = PyUnicode_InternFromString("javascript");

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
        name = javascript;
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
    context_c *py_ctx;
    static const char *keywords[] = {"context", "source", "filename", NULL};
    PyObject *source;
    PyObject *filename = Py_None;
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "OO|O", (char **) keywords, &py_ctx, &source, &filename) < 0) {
        return NULL;
    }
    IN_V8;
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
    PY_PROPAGATE_JS;
    Local<UnboundScript> script = maybe_script.ToLocalChecked();
    //script_c *self = (script_c *) type->tp_alloc(type, 0);
    script_c *self = (script_c *)PyType_GenericAlloc(type, 0);
    self->script.Reset(isolate, script);
    Py_INCREF(filename);
    self->script_name = filename;
    Py_INCREF(source);
    self->source = source;

    return (PyObject *) self;
}

MaybeLocal<UnboundScript> 
script_compile(Local<Context> context, PyObject *source, PyObject *filename)
{
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
    if (maybe_script.IsEmpty())
        return maybe_script;
    return hs.Escape(maybe_script.ToLocalChecked());
}

void 
script_dealloc(script_c *self)
{
    self->script.Reset();

    Py_XDECREF(self->script_name);
    Py_XDECREF(self->source);
    //Py_TYPE(self)->tp_free((PyObject *) self);
    TP_FREE_ABI3(script_type, self);
}

