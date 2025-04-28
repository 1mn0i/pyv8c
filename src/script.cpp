#include <Python.h>
#include "pyv8env.h"
#include <v8.h>

#include "convert.h"
#include "context.h"
#include "script.h"

using namespace v8;


PyObject *javascript;

PyTypeObject script_type = {
    PyVarObject_HEAD_INIT(NULL, 0)
};
int script_type_init() {

    javascript = PyString_InternFromString("javascript");

    script_type.tp_name = "pyv8env.Script";
    script_type.tp_basicsize = sizeof(script_c);
    script_type.tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_WEAKREFS;
    script_type.tp_doc = "v8 Script";
    script_type.tp_new = (newfunc) script_new;
    script_type.tp_dealloc = (destructor) script_dealloc;

    return PyType_Ready(&script_type);
}

PyObject *construct_script_name(Local<Context> context, Local<Value> js_name, int id) {
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

PyObject *script_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) {
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

    if (!PyString_Check(source)) {
        PyErr_SetString(PyExc_TypeError, "source must be a string");
        return NULL;
    }
    if (filename != Py_None && !PyString_Check(filename)) {
        PyErr_SetString(PyExc_TypeError, "filename must be a string or None");
        return NULL;
    }
    MaybeLocal<UnboundScript> maybe_script = script_compile(context, source, filename);
    PY_PROPAGATE_JS;
    Local<UnboundScript> script = maybe_script.ToLocalChecked();
    script_c *self = (script_c *) type->tp_alloc(type, 0);
    self->script.Reset(isolate, script);
    Py_INCREF(filename);
    self->script_name = filename;
    Py_INCREF(source);
    self->source = source;

    return (PyObject *) self;
}

MaybeLocal<UnboundScript> script_compile(Local<Context> context, PyObject *source, PyObject *filename) {
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

void script_dealloc(script_c *self) {
    self->script.Reset();

    Py_XDECREF(self->script_name);
    Py_XDECREF(self->source);
    Py_TYPE(self)->tp_free((PyObject *) self);
}

