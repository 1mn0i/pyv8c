// The Python face of a kernel isolate.
//
// It exists so a host can decide *where* an isolate lives: a worker that needs
// its own V8 heap creates one on its own thread, and everything that isolate
// later runs has to happen there.  Closing is explicit and only legal on that
// thread, which is the same rule the C++ side enforces.

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/isolate_type.h"
#include "module/registry.h"
#include "v8n/kernel.h"

namespace v8n
{
namespace
{

struct IsolateObject
{
    PyObject_HEAD

    Isolate* isolate;
};

int isolate_traverse(IsolateObject*, visitproc, void*)
{
    // An isolate holds no Python references: the objects it can reach are
    // reached through V8, and the realm registry invalidates them on close.
    return 0;
}

void isolate_dealloc(IsolateObject* self)
{
    PyObject_GC_UnTrack(self);
    if (self->isolate != nullptr)
    {
        // A dealloc cannot report a failure and may run on any thread, so an
        // isolate that is dropped outside its owner thread is parked for that
        // thread instead of being destroyed from here.
        Isolate::abandon(self->isolate);
        self->isolate = nullptr;
    }
    PyErr_Clear();
    abi3::FreeInstance(Py_TYPE(reinterpret_cast<PyObject*>(self)), self);
}

PyObject* isolate_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    static const char* keywords[] = {nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "", const_cast<char**>(keywords)))
        return nullptr;

    auto* self = reinterpret_cast<IsolateObject*>(PyType_GenericAlloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->isolate = Isolate::create();
    if (self->isolate == nullptr)
    {
        Py_DECREF(reinterpret_cast<PyObject*>(self));
        return nullptr;
    }
    return reinterpret_cast<PyObject*>(self);
}

Isolate* live_isolate(IsolateObject* self, const char* operation)
{
    if (self->isolate == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: the isolate is closed", operation);
        return nullptr;
    }
    return self->isolate;
}

PyObject* isolate_close(IsolateObject* self, PyObject*)
{
    if (self->isolate == nullptr)
        return abi3::NewRef(Py_False);
    if (!self->isolate->require_owner_thread("Isolate.close()"))
        return nullptr;

    Isolate::destroy(self->isolate);
    self->isolate = nullptr;
    return abi3::NewRef(Py_True);
}

PyObject* isolate_check_thread(IsolateObject* self, PyObject*)
{
    Isolate* isolate = live_isolate(self, "Isolate.check_thread()");
    if (isolate == nullptr)
        return nullptr;
    if (!isolate->require_owner_thread("Isolate.check_thread()"))
        return nullptr;
    return abi3::NewRef(Py_None);
}

PyObject* isolate_get_closed(IsolateObject* self, void*)
{
    return PyBool_FromLong(self->isolate == nullptr ? 1 : 0);
}

PyObject* isolate_get_owner_thread(IsolateObject* self, void*)
{
    Isolate* isolate = live_isolate(self, "Isolate.owner_thread");
    if (isolate == nullptr)
        return nullptr;
    return PyLong_FromUnsignedLongLong(isolate->owner_thread());
}

PyObject* isolate_get_live_handles(IsolateObject* self, void*)
{
    Isolate* isolate = live_isolate(self, "Isolate.live_handles");
    if (isolate == nullptr)
        return nullptr;
    return PyLong_FromSize_t(isolate->live_handles());
}

PyObject* isolate_repr(IsolateObject* self)
{
    if (self->isolate == nullptr)
        return PyUnicode_FromString("<v8n.Isolate closed>");
    return PyUnicode_FromFormat("<v8n.Isolate owner=0x%llx handles=%zu>",
                                self->isolate->owner_thread(),
                                self->isolate->live_handles());
}

PyMethodDef isolate_methods[] = {
    {"close", reinterpret_cast<PyCFunction>(isolate_close), METH_NOARGS,
     "close() -> bool\n\n"
     "Destroy the isolate.  Idempotent, and only legal on the thread that "
     "created it."},
    {"check_thread", reinterpret_cast<PyCFunction>(isolate_check_thread), METH_NOARGS,
     "check_thread() -> None\n\n"
     "Raise unless the calling thread is this isolate's owner."},
    {nullptr, nullptr, 0, nullptr},
};

PyGetSetDef isolate_getset[] = {
    {const_cast<char*>("closed"), reinterpret_cast<getter>(isolate_get_closed), nullptr,
     const_cast<char*>("Whether the isolate has been destroyed."), nullptr},
    {const_cast<char*>("owner_thread"), reinterpret_cast<getter>(isolate_get_owner_thread),
     nullptr, const_cast<char*>("Identity of the thread that created it."), nullptr},
    {const_cast<char*>("live_handles"),
     reinterpret_cast<getter>(isolate_get_live_handles), nullptr,
     const_cast<char*>("How many V8 handles this isolate still retains."), nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

PyType_Slot isolate_slots[] = {
    {Py_tp_new, reinterpret_cast<void*>(isolate_new)},
    {Py_tp_dealloc, reinterpret_cast<void*>(isolate_dealloc)},
    {Py_tp_traverse, reinterpret_cast<void*>(isolate_traverse)},
    {Py_tp_methods, isolate_methods},
    {Py_tp_getset, isolate_getset},
    {Py_tp_repr, reinterpret_cast<void*>(isolate_repr)},
    {Py_tp_doc, const_cast<char*>("An independent V8 isolate owned by its creating thread.")},
    {0, nullptr},
};

PyType_Spec isolate_spec = {
    "v8n.Isolate",
    sizeof(IsolateObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    isolate_slots,
};

}  // namespace

PyTypeObject* isolate_type_object = nullptr;

int
register_isolate_type(PyObject* module)
{
    isolate_type_object = reinterpret_cast<PyTypeObject*>(PyType_FromSpec(&isolate_spec));
    if (isolate_type_object == nullptr)
        return -1;
    if (!module::publish_type(module, "Isolate", isolate_type_object))
    {
        Py_DECREF(reinterpret_cast<PyObject*>(isolate_type_object));
        return -1;
    }
    return 0;
}

Isolate*
isolate_of(PyObject* object)
{
    if (object == nullptr || isolate_type_object == nullptr)
        return nullptr;
    if (!PyObject_TypeCheck(object, isolate_type_object))
        return nullptr;
    return reinterpret_cast<IsolateObject*>(object)->isolate;
}

}  // namespace v8n
