#include "bridge_base.h"
#include <Python.h>
#include "entry.h"
#include "execution.h"
#include "runtime.h"
#include "crossing.h"
#include <v8.h>

#include "convert.h"
#include "context.h"
#include "script.h"

using namespace v8;

namespace
{

// A character is at least a byte, and the stable ABI counts them without copying.
int64_t source_weight(PyObject* source)
{
    const Py_ssize_t length = PyUnicode_GetLength(source);
    return length > 0 ? static_cast<int64_t>(length) : 0;
}

}

PyTypeObject* script_type = NULL;

static int script_traverse(script_c* self, visitproc visit, void* arg) {
    Py_VISIT(self->py_context);
    Py_VISIT(self->py_isolate);
    Py_VISIT(self->source);
    Py_VISIT(self->script_name);
    return 0;
}

static int script_clear(script_c* self) {
    std::unique_ptr<IsolateLockScope> lock;
    if (self->isolate_state) lock.reset(new IsolateLockScope(self->isolate_state));
    self->registration.detach();
    self->script.Reset();
    if (self->isolate_state != nullptr && self->source != nullptr)
        self->isolate_state->account_external_memory(-source_weight(self->source));
    self->isolate_state = nullptr;
    Py_CLEAR(self->script_name);
    Py_CLEAR(self->source);
    Py_CLEAR(self->py_context);
    Py_CLEAR(self->py_isolate);
    return 0;
}

static PyType_Slot script_slots[] = {
    {Py_tp_traverse, (void*)PythonBoundary<script_traverse>::call},
    {Py_tp_clear, (void*)PythonBoundary<script_clear>::call},
    {Py_tp_doc, (void*)"v8 Script"},
    {Py_tp_new, (void *)PythonBoundary<script_new>::call},
    {Py_tp_dealloc, (void *)PythonBoundary<script_dealloc>::call},
    {0, NULL}};

static PyType_Spec script_spec = {
    "pyv8.Script",
    sizeof(script_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
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
script_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    if (!runtime_manager.ensure_initialized())
        return NULL;
    context_c *py_ctx;
    static const char *keywords[] = {"context", "source", "filename", NULL};
    PyObject *source;
    PyObject *filename = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|O", (char **) keywords, &py_ctx, &source, &filename)) {
        return NULL;
    }
    if (!PyObject_TypeCheck((PyObject*)py_ctx, context_type)) {
        PyErr_SetString(PyExc_TypeError, "context must be an active Context instance");
        return NULL;
    }
    ExecutionScope execution(py_ctx);
    if (!execution) return nullptr;
    auto* isolate_state = py_ctx->isolate_state;
    auto* isolate = execution.isolate();
    auto context = execution.context();
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
    script_c *self = (script_c *)PyType_GenericAlloc(type, 0);
    PyErr_PROPAGATE(self);
    self->isolate_state = isolate_state;
    self->py_context = (PyObject*)py_ctx;
    Py_INCREF((PyObject*)py_ctx);
    self->registration.attach(py_ctx, (PyObject*)self, (int (*)(PyObject*))script_clear);
    self->script.Set(isolate_state, isolate, script);
    if (py_ctx->py_isolate != nullptr) {
        self->py_isolate = py_ctx->py_isolate;
        Py_INCREF(self->py_isolate);
    }
    Py_INCREF((PyObject*)filename);
    self->script_name = filename;
    Py_INCREF((PyObject*)source);
    self->source = source;
    isolate_state->account_external_memory(source_weight(source));

    return (PyObject *) self;
}

MaybeLocal<UnboundScript> 
script_compile(Local<Context> context, PyObject *source, PyObject *filename)
{
    Isolate* isolate = v8_compat::context_isolate(context);
    EscapableHandleScope hs(isolate);
    MaybeLocal<UnboundScript> maybe_script;
    if (filename != Py_None) {
        ScriptOrigin origin(js_from_py(filename, context));
        ScriptCompiler::Source js_source(js_from_py(source, context).As<String>(), origin);
        maybe_script = without_gil([&] { return ScriptCompiler::CompileUnboundScript(isolate, &js_source); });
    } else {
        ScriptCompiler::Source js_source(js_from_py(source, context).As<String>());
        maybe_script = without_gil([&] { return ScriptCompiler::CompileUnboundScript(isolate, &js_source); });
    }
    Local<UnboundScript> script;
    if (!maybe_script.ToLocal(&script))
        return maybe_script;
    return hs.Escape(script);
}

void 
script_dealloc(script_c *self)
{
    PyObject_GC_UnTrack(self);
    script_clear(self);
    TP_FREE_ABI3(script_type, self);
}
