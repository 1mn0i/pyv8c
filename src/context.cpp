#include <Python.h>
#include "pyv8.h"
#include <v8.h>

#ifdef _WIN32
#include <Windows.h>
#else
#include <pthread.h>
#endif

#include "context.h"
#include "script.h"
#include "convert.h"
#include "jsobject.h"
#include "pyclass.h"
#include "pyfunction.h"
#include "jsfunction.h"

using namespace v8;

PyMethodDef context_methods[] = {
    {"exec_js", (PyCFunction)context_run, METH_VARARGS | METH_KEYWORDS, NULL},
    {"js_obj", (PyCFunction)context_js_obj, METH_O, NULL},
    {"expose", (PyCFunction)context_expose, METH_VARARGS | METH_KEYWORDS, NULL},
    {"expose_module", (PyCFunction)context_expose_module, METH_O, NULL},
    {"gc", (PyCFunction)context_gc, METH_NOARGS, NULL},
    {NULL},
};
// Python is wrong. The first entry is not modifiable and should be const char *
PyGetSetDef context_getset[] = {
    {(char*)"glob", (getter)context_get_global, NULL, NULL, NULL},
    {(char*)"timeout", (getter)context_get_timeout, (setter)context_set_timeout, NULL, NULL},
    {NULL},
};
// We don't need mapping methods for ABI3 compatibility

// For ABI3 compatibility, we'll define the type using PyType_Spec
static PyType_Slot context_slots[] = {
    {Py_tp_dealloc, (void*)context_dealloc},
    {Py_tp_new, (void*)context_new},
    {Py_tp_methods, context_methods},
    {Py_tp_getattro, (void*)context_getattro},
    {Py_tp_setattro, (void*)context_setattro},
    {Py_tp_getset, context_getset},
    {Py_tp_doc, "v8 context"},
    // We can't use Py_tp_as_mapping in Limited API
    // {Py_tp_as_mapping, &context_mapping_for_spec},
    {0, NULL} };

static PyType_Spec context_spec = {
    "_pyv8env.Context",
    sizeof(context_c),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    context_slots };

PyTypeObject* context_type = NULL;

int context_type_init()
{
    // Create the type using PyType_FromSpec for ABI3 compatibility
    context_type = (PyTypeObject *) PyType_FromSpec(&context_spec);
    if (context_type == NULL)
    {
        return -1;
    }
    // We don't want to DECREF here because we need to keep the type object alive
    return 0;
}


PyObject *
context_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    IN_V8;

    double timeout = 0;
    PyObject* global = NULL;
    PyObject* hook = Py_False;
    PyObject* ctx_type = NULL;
    static const char *keywords[] = {"global_this", "hook", "ctx_type", "timeout", NULL};

    if (PyArg_ParseTupleAndKeywords(args, kwargs, "O|OOd", (char **) keywords, &global, &hook, &ctx_type, &timeout) < 0) {
        return NULL;
    }

    if (global != NULL) {
        if (PyType_Check(global)) {
            PyObject* no_args = PyTuple_New(0);
            PyErr_PROPAGATE(no_args);
            global = PyObject_Call(global, no_args, NULL);
            Py_XDECREF(no_args);
            PyErr_PROPAGATE(global);
        }
        else {
            Py_INCREF(global);
        }
    }

    context_c* self = (context_c*)PyType_GenericAlloc(type, 0);
    self->has_debugger = false;
    self->has_done = false;
    self->timeout = timeout;
    self->hook = hook == Py_True;

    if (ctx_type == NULL) {
        ctx_type = PyUnicode_FromString("Top");
    }

    PyErr_PROPAGATE(self);
    
    MaybeLocal<ObjectTemplate> global_template;
    if (global != NULL) {

        PyObject* global_type;
        if (!PyType_Check(global)) {
        //if (PyInstance_Check(global)) {
            global_type = PyObject_GetAttrString(global, "__class__");
        }
        else {
            global_type = (PyObject*)Py_TYPE(global);
            Py_INCREF(global_type);
        }
        py_class* templ;
        templ = (py_class*)py_class_to_template(global_type, self);
        Py_XDECREF(global_type);
        Local<FunctionTemplate> global_inteface_template = templ->templ.Get(isolate);
        global_template = global_inteface_template->InstanceTemplate();

    }

    IN_CONTEXT(Context::New(isolate, NULL, global_template));
    self->js_context.Reset(isolate, context);
    self->has_done = true;

    // add env to vm
    //IN_CONTEXT(self->js_context.Get(isolate));
    context->SetEmbedderData(CTX_TYPE, V8String(isolate, PyUnicode_AsUTF8_abi3(ctx_type)));
    context->SetEmbedderData(CONTEXT_OBJECT_SLOT, External::New(isolate, self));
    context->SetEmbedderData(OBJECT_PROTOTYPE_SLOT, Object::New(isolate)->GetPrototype());
    context->SetEmbedderData(ERROR_PROTOTYPE_SLOT, Exception::Error(String::Empty(isolate)).As<Object>()->GetPrototype());

    PyObject *weakref_module = PyImport_ImportModule("weakref");
    PyErr_PROPAGATE(weakref_module);
    PyObject *weak_key_dict = PyObject_GetAttrString(weakref_module, "WeakKeyDictionary");
    PyErr_PROPAGATE(weak_key_dict);
    self->js_object_cache = PyObject_CallObject(weak_key_dict, NULL);
    PyErr_PROPAGATE(self->js_object_cache);

    self->scripts = PySet_New(NULL);
    PyErr_PROPAGATE(self->scripts);

    if (global != NULL) {
        self->py_global = global;
        py_class_init_js_object(context->Global()->GetPrototype().As<Object>(), global, context);
    }
    return (PyObject *) self;
}

void 
context_dealloc(context_c *self)
{
    self->js_context.Reset();
    Py_XDECREF(self->py_global);
    Py_XDECREF(self->scripts);

    if (PyObject_HasAttrString(self->js_object_cache, "items")) {
        PyObject* items = PyObject_CallMethod(self->js_object_cache, "items", nullptr);
        if (items) {
            PyObject* iterator = PyObject_GetIter(items);
            if (iterator) {
                PyObject* item;
                while ((item = PyIter_Next(iterator)) != nullptr) {
                    Py_DECREF(item);
                }
                Py_DECREF(iterator);
            }
            Py_DECREF(items);
        }
    }

    Py_XDECREF(self->js_object_cache);

    //Py_TYPE(self)->tp_free((PyObject *) self);
    TP_FREE_ABI3(context_type, self);
}

PyObject *
context_expose(context_c *self, PyObject *args, PyObject *kwargs)
{
    IN_V8;
    Local<Context> context = self->js_context.Get(isolate);
    Local<Object> global = context->Global();

    args = PySequence_Fast(args, "sequence required");
    PyErr_PROPAGATE(args);
    Py_ssize_t args_size = PySequence_Size(args);
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        PyErr_PROPAGATE(object);
        if (!PyObject_HasAttrString(object, "__name__"))
        {
            PyErr_SetString(PyExc_TypeError, "Object passed to expose must have a __name__");
            Py_DECREF(object);
            return NULL;
        }
        Py_DECREF(object);
    }
    for (int i = 0; i < args_size; i++)
    {
        PyObject *object = PySequence_GetItem(args, i);
        PyErr_PROPAGATE(object);
        PyObject *name = PyObject_GetAttrString(object, "__name__");
        PyErr_PROPAGATE(name);
        global->CreateDataProperty(context, js_from_py(name, context).As<String>(), js_from_py(object, context));
        Py_DECREF(object);
        Py_DECREF(name);
    }
    Py_XDECREF(args);

    if (kwargs != NULL)
    {
        PyObject *name, *object;
        Py_ssize_t pos = 0;
        while (PyDict_Next(kwargs, &pos, &name, &object))
        {
            global->CreateDataProperty(context, js_from_py(name, context).As<String>(), js_from_py(object, context));
        }
    }

    Py_RETURN_NONE;
}

PyObject *
context_expose_module(context_c *self, PyObject *module)
{
    if (!PyModule_Check(module)) {
        PyErr_SetString(PyExc_TypeError, "context_expose_module requires a module");
        return NULL;
    }

    PyObject *module_all_slow = PyObject_Dir(module);
    PyErr_PROPAGATE(module_all_slow);
    PyObject *module_all = PySequence_Fast(module_all_slow, "o noes");
    Py_XDECREF(module_all_slow);
    PyErr_PROPAGATE(module_all);
    PyObject *members = PyDict_New();
    PyErr_PROPAGATE(members);
    Py_ssize_t module_all_size = PySequence_Size(module_all);
    for (int i = 0; i < module_all_size; i++)
    {
        PyObject *name = PySequence_GetItem(module_all, i);
        if (!PyString_StartsWithString(name, "_"))
        {
            PyObject *value = PyObject_GetAttr(module, name);
            if (value == NULL)
            {
                Py_DECREF(name);
                Py_XDECREF(members);
                return NULL;
            }
            if (PyDict_SetItem(members, name, value) < 0)
            {
                Py_DECREF(name);
                Py_DECREF(value);
                Py_XDECREF(members);
                return NULL;
            }
            Py_DECREF(value);
        }
        Py_DECREF(name);
    }

    PyObject *no_args = PyTuple_New(0);
    PyErr_PROPAGATE(no_args);
    PyObject *result = context_expose(self, no_args, members);
    Py_XDECREF(no_args);
    Py_XDECREF(members);
    return result;
}

PyObject* 
context_js_obj(context_c* self, PyObject* py_object)
{
    if (PyMapping_HasKey(self->js_object_cache, py_object)) {
        PyObject* jsobj = PyObject_GetItem(self->js_object_cache, py_object);
        if (jsobj == NULL) {
            PyErr_WriteUnraisable(PyUnicode_InternFromString("pyv8env py_class_create_js_object getitem"));
        }
        Py_INCREF(jsobj);
        return jsobj;
    }
    Py_RETURN_NONE;
}

#ifdef _WIN32

UINT s_timer_id;

static void CALLBACK 
breaker_callback(UINT uTimerID, UINT uMsg, DWORD dwUser, DWORD dw1, DWORD dw2)
{
    isolate->TerminateExecution();
}

#else

pthread_t breaker_id;
useconds_t s_timeout;

void *breaker_thread(void *param) {
    useconds_t timeout = *(useconds_t *) param;
    usleep(timeout);
    pthread_setcancelstate(PTHREAD_CANCEL_DISABLE, NULL);
    isolate->TerminateExecution();
    return NULL;
}
#endif

static double 
context_timeout(Local<Context> context)
{
    context_c *ctx_c = (context_c *) context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    return ctx_c->timeout;
}

static bool 
setup_timeout(double timeout)
{
#ifdef _WIN32
    if (timeout > 0) {
        UINT timeout_ = (UINT) (timeout * 1000);
        s_timer_id = timeSetEvent(timeout_, 0, (LPTIMECALLBACK) breaker_callback, 0,
            TIME_ONESHOT | TIME_CALLBACK_FUNCTION | TIME_KILL_SYNCHRONOUS);
        if (s_timer_id == NULL) {
            PyErr_SetFromErrno(PyExc_OSError);
            return false;
        }
    }
#else
    if (timeout > 0) {
        s_timeout = (useconds_t) (timeout * 1000000);
        errno = pthread_create(&breaker_id, NULL, breaker_thread, &s_timeout);
        if (errno) {
            PyErr_SetFromErrno(PyExc_OSError);
            return false;
        }
    }
#endif
    return true;
}

static bool 
cleanup_timeout(double timeout)
{
#ifdef _WIN32
    if (timeout > 0 && s_timer_id != NULL) {
        timeKillEvent(s_timer_id);
    }
#else
    if (timeout > 0) {
        pthread_cancel(breaker_id);
        errno = pthread_join(breaker_id, NULL);
        if (errno) {
            PyErr_SetFromErrno(PyExc_OSError);
            return false;
        }
    }
#endif
    return true;
}

bool 
context_setup_timeout(Local<Context> context)
{
    return setup_timeout(context_timeout(context));
}

bool 
context_cleanup_timeout(Local<Context> context)
{
    return cleanup_timeout(context_timeout(context));
}

PyObject *
context_run(context_c *self, PyObject *args, PyObject *kwargs)
{
    PyObject *program;
    PyObject *filename = Py_None;
    double timeout = self->timeout;
    static const char *keywords[] = {"program", "filename", "timeout", NULL};
    // python needs to fix their shit and make it const
    if (PyArg_ParseTupleAndKeywords(args, kwargs, "O|Od", (char **) keywords, &program, &filename, &timeout) < 0) {
        return NULL;
    }
    if (!PyUnicode_Check(program) && !PyObject_IsInstance(program, (PyObject *)script_type))
    {
        PyErr_SetString(PyExc_TypeError, "program must be a string or Script");
        return NULL;
    }
    if (PyUnicode_Check(program))
    {
        program = PyObject_CallFunctionObjArgs((PyObject *)script_type, (PyObject*)self, program, filename, NULL);
        PyErr_PROPAGATE(program);
    }
    else
    {
        Py_INCREF(program);
    }
    assert(PyObject_IsInstance(program, (PyObject*)script_type));
    script_c *py_script = (script_c *) program;

    IN_V8;
    IN_CONTEXT(self->js_context.Get(isolate));
    JS_TRY

    PySet_Add(self->scripts, program);
    Local<UnboundScript> unbound_script;
    if (self->has_debugger) {
        MaybeLocal<UnboundScript> maybe_script = script_compile(context, py_script->source, py_script->script_name);
        PY_PROPAGATE_JS;
        unbound_script = maybe_script.ToLocalChecked();
    } else {
        unbound_script = py_script->script.Get(isolate);
    }
    Py_XDECREF(program);
    Local<Script> script = unbound_script->BindToCurrentContext();

    if (!setup_timeout(timeout)) return NULL;
    MaybeLocal<Value> result = script->Run(context);

    if (!cleanup_timeout(timeout)) return NULL;
    PY_PROPAGATE_JS;
    return py_from_js(result.ToLocalChecked(), context);
}


Local<Object> 
context_get_cached_jsobject(Local<Context> js_context, PyObject *py_object)
{
    EscapableHandleScope hs(isolate);
    context_c *self = (context_c *) js_context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    if (PyMapping_HasKey(self->js_object_cache, py_object)) {
        js_object *jsobj = (js_object *) PyObject_GetItem(self->js_object_cache, py_object);
        if (jsobj == NULL) {
            PyErr_WriteUnraisable(PyUnicode_InternFromString("pyv8env py_class_create_js_object getitem"));
        }
        return hs.Escape(jsobj->object.Get(isolate));
    }
    return Local<Object>();
}

void 
context_set_cached_jsobject(Local<Context> js_context, PyObject *py_object, Local<Object> object)
{
    EscapableHandleScope hs(isolate);
    context_c *self = (context_c *) js_context->GetEmbedderData(CONTEXT_OBJECT_SLOT).As<External>()->Value();
    js_object *jsobj = js_object_weak_new(object, js_context);
    if (jsobj == NULL) {
        return;
    }
    if (PyObject_SetItem(self->js_object_cache, py_object, (PyObject *) jsobj) < 0) {
        if (PyErr_ExceptionMatches(PyExc_TypeError)) {
            // if it's a type error, it's probably "cannot create weak reference" and should be ignored.
            PyErr_Clear();
        } else {
            PyErr_WriteUnraisable(PyUnicode_InternFromString("pyv8env py_class_create_js_object setitem"));
        }
    }
    Py_DECREF((PyObject *)jsobj);
}

PyObject *
context_get_timeout(context_c *self, void *shit)
{
    return PyFloat_FromDouble(self->timeout);
}

int *
context_set_timeout(context_c *self, PyObject *value, void *shit)
{
    self->timeout = PyFloat_AsDouble(value);
    return 0;
}

PyObject *
context_get_global(context_c *self, void *shit)
{
    IN_V8;
    Local<Context> context = self->js_context.Get(isolate);
    PyObject* pyobj= py_from_js(context->Global()->GetPrototype(), context);
    PyErr_PROPAGATE(pyobj);
    PyObject* result = context_js_obj(self, pyobj);
    Py_DECREF(pyobj);
    return result;
}

PyObject *
context_getattro(context_c *self, PyObject *name)
{
    PyObject *value = PyObject_GenericGetAttr((PyObject *) self, name);
    if (value == NULL) {
        PyErr_Clear();
        return context_getitem(self, name);
    }
    return value;
}

PyObject *
context_getitem(context_c *self, PyObject *name)
{
    PyObject* global = context_get_global(self, NULL);
    PyErr_PROPAGATE(global);
    return PyObject_GetAttr(global, name);
}

int 
context_setattro(context_c *self, PyObject *name, PyObject *value)
{
    // use GenericGetAttr to find out if Context defines the property
    PyObject *ctx_value = PyObject_GenericGetAttr((PyObject *) self, name);
    if (ctx_value == NULL) {
        // if the property is not defined by Context, set it on the global
        PyErr_Clear();
        return context_setitem(self, name, value);
    } else {
        Py_XDECREF(ctx_value);
    }
    // if the property is defined by Context, delegate
    return PyObject_GenericSetAttr((PyObject *) self, name, value);
}

int 
context_setitem(context_c *self, PyObject *name, PyObject *value)
{
    PyObject *global = context_get_global(self, NULL);
    PyErr_PROPAGATE_(global);
    return PyObject_SetAttr(global, name, value);
}

PyObject *
context_gc(context_c *self) 
{
    IN_V8;
    isolate->RequestGarbageCollectionForTesting(Isolate::GarbageCollectionType::kFullGarbageCollection);
    Py_RETURN_NONE;
}
