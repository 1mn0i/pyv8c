// The Python face of a realm.
//
// It owns the isolate when it had to create one, and borrows it when the host
// supplied one, because a host that wants several realms on one isolate says so
// by passing the isolate in.

#include "realm/realm_type.h"

#include "abi3/abi3_shim.h"
#include "boundary/guard.h"
#include "kernel/isolate_type.h"
#include "module/registry.h"
#include "v8n/boundary.h"
#include "v8n/bridge.h"
#include "v8n/realm.h"

namespace v8n
{
namespace
{

struct RealmObject
{
    PyObject_HEAD

    Realm* realm;
    // Non-null only when this object created the isolate and therefore has to
    // destroy it.
    Isolate* owned_isolate;
    PyObject* isolate_object;
};

RealmObject* self_of(PyObject* object)
{
    return reinterpret_cast<RealmObject*>(object);
}

void realm_dealloc(RealmObject* self)
{
    PyObject_GC_UnTrack(self);
    if (self->realm != nullptr)
    {
        // The realm holds Python references (host object wrappers), so its
        // teardown may re-enter Python.  Draining them here, while this object
        // is still on the stack, is why close() is separate from dealloc.
        if (self->realm->isolate().is_owner_thread())
            self->realm->close();
        else
            PyErr_Clear();
        Realm::destroy(self->realm);
        self->realm = nullptr;
    }
    if (self->owned_isolate != nullptr)
    {
        if (self->owned_isolate->is_owner_thread())
            Isolate::destroy(self->owned_isolate);
        else
            Isolate::abandon(self->owned_isolate);
        self->owned_isolate = nullptr;
    }
    Py_CLEAR(self->isolate_object);
    PyErr_Clear();
    abi3::FreeInstance(Py_TYPE(reinterpret_cast<PyObject*>(self)), self);
}

int realm_traverse(RealmObject* self, visitproc visit, void* arg)
{
    VN_VISIT(self->isolate_object);

    // The realm owns Python references of its own -- the error hooks, the trace
    // hook -- and they are reachable only through here.  Without this, a hook
    // that closes over the realm, or over any object that reaches the realm,
    // would be a cycle the collector cannot see through.
    if (self->realm != nullptr)
    {
        const int result = self->realm->traverse(visit, arg);
        if (result != 0)
            return result;
    }
    return 0;
}

int realm_clear(RealmObject* self)
{
    Py_CLEAR(self->isolate_object);
    return 0;
}

PyObject* realm_new(PyTypeObject* type, PyObject* args, PyObject* kwargs)
{
    PyObject* isolate_object = Py_None;
    PyObject* name = Py_None;
    int microtasks = 1;
    long long timeout = 0;
    PyObject* global_this = Py_None;
    PyObject* description = Py_None;
    static const char* keywords[] = {"isolate", "name",   "microtasks", "timeout",
                                     "global_this", "description", nullptr};
    // "O" for the name, not "p": p writes an int where a PyObject* is expected,
    // and the pointer that survives is whatever the int's high bits landed on.
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OOpLOO", const_cast<char**>(keywords),
                                     &isolate_object, &name, &microtasks, &timeout, &global_this,
                                     &description))
        return nullptr;

    Isolate* isolate = nullptr;
    bool owned = false;
    if (isolate_object == Py_None)
    {
        isolate = Isolate::create();
        if (isolate == nullptr)
            return nullptr;
        owned = true;
    }
    else
    {
        isolate = isolate_of(isolate_object);
        if (isolate == nullptr)
        {
            raise(PyExc_TypeError, "isolate must be a v8n.Isolate or None");
            return nullptr;
        }
    }

    RealmOptions options;
    if (name != Py_None)
    {
        options.name = abi3::Utf8(name);
        if (options.name == nullptr)
        {
            if (owned)
                Isolate::destroy(isolate);
            return nullptr;
        }
    }
    options.microtasks = microtasks != 0;
    options.timeout_ms = timeout;
    // Borrowed, and only read during creation: the global object and the batch
    // have to be in hand before the context exists.
    options.global_this = global_this == Py_None ? nullptr : global_this;
    options.description = description == Py_None ? nullptr : description;

    auto* self = reinterpret_cast<RealmObject*>(PyType_GenericAlloc(type, 0));
    if (self == nullptr)
    {
        if (owned)
            Isolate::destroy(isolate);
        return nullptr;
    }

    // Realm::create copies the name, so the Python string behind it is released
    // with the argument tuple and the realm is unaffected.
    self->realm = Realm::create(isolate, options);
    if (self->realm == nullptr)
    {
        // Parked across the decref, because dealloc clears the error state and the
        // caller has to hear why creation failed.  Without this, every failure
        // here -- including a bad description and an ICU mismatch -- reaches Python
        // as "returned NULL without setting an exception".
        PyObject* error_type = nullptr;
        PyObject* error_value = nullptr;
        PyObject* error_traceback = nullptr;
        PyErr_Fetch(&error_type, &error_value, &error_traceback);

        if (owned)
            Isolate::destroy(isolate);
        Py_DECREF(reinterpret_cast<PyObject*>(self));

        PyErr_Restore(error_type, error_value, error_traceback);
        return nullptr;
    }

    self->owned_isolate = owned ? isolate : nullptr;
    Py_INCREF(isolate_object);
    self->isolate_object = isolate_object;
    // The realm reaches back so a handle can keep the Python object that owns
    // the realm alive without the value layer needing a registry.
    self->realm->set_python_object(reinterpret_cast<PyObject*>(self));
    return reinterpret_cast<PyObject*>(self);
}

Realm* live_realm(RealmObject* self, const char* operation)
{
    if (self->realm == nullptr)
    {
        raise(PyExc_RuntimeError, "%s: the realm is closed", operation);
        return nullptr;
    }
    return self->realm;
}

PyObject* realm_evaluate(RealmObject* self, PyObject* args, PyObject* kwargs)
{
    Realm* realm = live_realm(self, "evaluate()");
    if (realm == nullptr)
        return nullptr;

    PyObject* source = nullptr;
    PyObject* filename = Py_None;
    static const char* keywords[] = {"source", "filename", nullptr};
    // "O" for the filename rather than "U": None means "no name", and every
    // other optional string in this API says the same thing the same way.
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U|O", const_cast<char**>(keywords),
                                     &source, &filename))
        return nullptr;

    const char* text = abi3::Utf8(source);
    if (text == nullptr)
        return nullptr;

    const char* name = nullptr;
    if (filename != Py_None)
    {
        if (!PyUnicode_Check(filename))
            return raise(PyExc_TypeError, "filename must be a string or None");
        name = abi3::Utf8(filename);
        if (name == nullptr)
            return nullptr;
    }
    return realm->evaluate(text, name);
}

PyObject* realm_close(RealmObject* self, PyObject*)
{
    if (self->realm == nullptr)
        return abi3::NewRef(Py_False);
    if (!self->realm->close())
        return nullptr;
    return abi3::NewRef(Py_True);
}

PyObject* realm_get_global(RealmObject* self, void*)
{
    Realm* realm = live_realm(self, "global");
    if (realm == nullptr)
        return nullptr;
    return realm->global_object();
}

PyObject* realm_get_closed(RealmObject* self, void*)
{
    return PyBool_FromLong(self->realm == nullptr || self->realm->is_closed() ? 1 : 0);
}

PyObject* realm_get_active(RealmObject* self, void*)
{
    return PyBool_FromLong(self->realm != nullptr && self->realm->is_active() ? 1 : 0);
}

PyObject* realm_get_isolate(RealmObject* self, void*)
{
    if (self->isolate_object == nullptr)
        return abi3::NewRef(Py_None);
    return abi3::NewRef(self->isolate_object);
}

PyObject* realm_repr(RealmObject* self)
{
    if (self->realm == nullptr)
        return PyUnicode_FromString("<v8n.Realm closed>");
    return PyUnicode_FromFormat("<v8n.Realm %s>",
                                self->realm->is_active() ? "active" : "closed");
}

PyObject* realm_wrap(RealmObject* self, PyObject* object)
{
    Realm* realm = live_realm(self, "wrap()");
    if (realm == nullptr)
        return nullptr;
    return realm->wrap(object);
}

PyObject* realm_set_type_resolver(RealmObject* self, PyObject* callable)
{
    Realm* realm = live_realm(self, "set_type_resolver()");
    if (realm == nullptr)
        return nullptr;
    if (!realm->set_type_resolver(callable == Py_None ? nullptr : callable))
        return nullptr;
    return abi3::NewRef(Py_None);
}

PyObject* realm_install(RealmObject* self, PyObject* description)
{
    Realm* realm = live_realm(self, "install()");
    if (realm == nullptr)
        return nullptr;
    if (!install_batch(*realm, description))
        return nullptr;
    return abi3::NewRef(Py_None);
}

PyObject* realm_set_error_hooks(RealmObject* self, PyObject* args, PyObject* kwargs)
{
    Realm* realm = live_realm(self, "set_error_hooks()");
    if (realm == nullptr)
        return nullptr;

    PyObject* on_uncaught = Py_None;
    PyObject* on_rejection = Py_None;
    static const char* keywords[] = {"on_uncaught", "on_rejection", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|OO", const_cast<char**>(keywords),
                                     &on_uncaught, &on_rejection))
        return nullptr;

    PyObject* uncaught = on_uncaught == Py_None ? nullptr : on_uncaught;
    PyObject* rejection = on_rejection == Py_None ? nullptr : on_rejection;
    if (!realm->set_error_hooks(uncaught, rejection))
        return nullptr;
    return abi3::NewRef(Py_None);
}

PyObject* realm_enqueue_microtask(RealmObject* self, PyObject* callable)
{
    Realm* realm = live_realm(self, "enqueue_microtask()");
    if (realm == nullptr)
        return nullptr;
    if (!realm->enqueue_microtask(callable))
        return nullptr;
    return abi3::NewRef(Py_None);
}

PyObject* realm_run_microtasks(RealmObject* self, PyObject*)
{
    Realm* realm = live_realm(self, "run_microtasks()");
    if (realm == nullptr)
        return nullptr;
    if (!realm->run_microtasks())
        return nullptr;
    return abi3::NewRef(Py_True);
}

PyObject* realm_get_microtasks(RealmObject* self, void*)
{
    return PyBool_FromLong(self->realm != nullptr && self->realm->microtasks_supported() ? 1 : 0);
}

PyObject* realm_get_root_count(RealmObject* self, void*)
{
    if (self->realm == nullptr)
        return PyLong_FromLong(0);
    return PyLong_FromSize_t(self->realm->root_count());
}

PyObject* realm_get_timeout(RealmObject* self, void*)
{
    if (self->realm == nullptr)
        return PyLong_FromLong(0);
    return PyLong_FromLongLong(self->realm->timeout());
}

int realm_set_timeout(RealmObject* self, PyObject* value, void*)
{
    Realm* realm = live_realm(self, "timeout");
    if (realm == nullptr)
        return -1;
    if (value == nullptr)
    {
        raise(PyExc_AttributeError, "timeout cannot be deleted");
        return -1;
    }

    const long long milliseconds = PyLong_AsLongLong(value);
    if (milliseconds == -1 && PyErr_Occurred())
        return -1;
    return realm->set_timeout(milliseconds) ? 0 : -1;
}

PyObject* realm_get_trace_hook(RealmObject* self, void*)
{
    if (self->realm == nullptr)
        return abi3::NewRef(Py_None);
    PyObject* hook = self->realm->trace_hook();
    if (hook == nullptr)
        return abi3::NewRef(Py_None);
    return abi3::NewRef(hook);
}

int realm_set_trace_hook(RealmObject* self, PyObject* value, void*)
{
    Realm* realm = live_realm(self, "trace_hook");
    if (realm == nullptr)
        return -1;
    const bool installed = realm->set_trace_hook(value == Py_None ? nullptr : value);
    return installed ? 0 : -1;
}

PyObject* realm_get_terminated(RealmObject* self, void*)
{
    return PyBool_FromLong(self->realm != nullptr && self->realm->was_terminated() ? 1 : 0);
}

PyMethodDef realm_methods[] = {
    {"evaluate", reinterpret_cast<PyCFunction>(realm_evaluate),
     METH_VARARGS | METH_KEYWORDS,
     "evaluate(source, filename=None) -> object\n\n"
     "Compile and run source, returning the value through the three-state "
     "boundary."},
    {"close", reinterpret_cast<PyCFunction>(realm_close), METH_NOARGS,
     "close() -> bool\n\n"
     "Release everything the realm owns.  Idempotent, and callable again to "
     "finish a close that was refused because the caller was not the isolate's "
     "owner thread."},
    {"wrap", reinterpret_cast<PyCFunction>(realm_wrap), METH_O,
     "wrap(object) -> Value\n\n"
     "A handle on a Python object's JavaScript counterpart.  The same object "
     "always answers with the same JavaScript value."},
    {"set_type_resolver", reinterpret_cast<PyCFunction>(realm_set_type_resolver), METH_O,
     "set_type_resolver(callable) -> None\n\n"
     "Install the callable asked to prepare a Python object whose type has no "
     "interface in this realm, just before the object is wrapped for "
     "JavaScript.  Passing None removes it."},
    {"install", reinterpret_cast<PyCFunction>(realm_install), METH_O,
     "install(description) -> None\n\n"
     "Install a whole batch of interfaces in one crossing.  The description is "
     "validated and every template is built before anything is written into the "
     "realm, so a description that is wrong installs nothing."},
    {"set_error_hooks", reinterpret_cast<PyCFunction>(realm_set_error_hooks),
     METH_VARARGS | METH_KEYWORDS,
     "set_error_hooks(on_uncaught=None, on_rejection=None) -> None\n\n"
     "Install the hooks that report failures JavaScript produced where no Python "
     "caller is waiting.  Passing None removes a hook.  Both are held by the "
     "realm and released when it closes."},
    {"enqueue_microtask", reinterpret_cast<PyCFunction>(realm_enqueue_microtask), METH_O,
     "enqueue_microtask(callable) -> None\n\n"
     "Queue a callable to run at the next microtask checkpoint.  Refuses when "
     "this realm has no microtask queue."},
    {"run_microtasks", reinterpret_cast<PyCFunction>(realm_run_microtasks), METH_NOARGS,
     "run_microtasks() -> bool\n\n"
     "Drain the microtask queue.  Runs automatically at the end of every "
     "evaluate()."},
    {nullptr, nullptr, 0, nullptr},
};

PyGetSetDef realm_getset[] = {
    {const_cast<char*>("closed"), reinterpret_cast<getter>(realm_get_closed), nullptr,
     const_cast<char*>("Whether the realm has been closed."), nullptr},
    {const_cast<char*>("glob"), reinterpret_cast<getter>(realm_get_global), nullptr,
     const_cast<char*>("The realm's global object, as a handle.  Named glob because "
                       "global is a Python keyword, and because that is the name the "
                       "previous binding used."),
     nullptr},
    {const_cast<char*>("is_active"), reinterpret_cast<getter>(realm_get_active), nullptr,
     const_cast<char*>("Whether the realm still accepts entry."), nullptr},
    {const_cast<char*>("isolate"), reinterpret_cast<getter>(realm_get_isolate), nullptr,
     const_cast<char*>("The isolate this realm runs on."), nullptr},
    {const_cast<char*>("microtasks"), reinterpret_cast<getter>(realm_get_microtasks), nullptr,
     const_cast<char*>("Whether this realm can run microtasks."), nullptr},
    {const_cast<char*>("root_count"), reinterpret_cast<getter>(realm_get_root_count), nullptr,
     const_cast<char*>("How many Python references this realm holds on JavaScript's "
                       "behalf.  Zero after a close."),
     nullptr},
    {const_cast<char*>("timeout"), reinterpret_cast<getter>(realm_get_timeout),
     reinterpret_cast<setter>(realm_set_timeout),
     const_cast<char*>("Wall-clock budget for one entry, in milliseconds.  0 means no "
                       "limit."),
     nullptr},
    {const_cast<char*>("trace_hook"), reinterpret_cast<getter>(realm_get_trace_hook),
     reinterpret_cast<setter>(realm_set_trace_hook),
     const_cast<char*>("Called with (event, member, detail) for every access the "
                       "binding dispatches."),
     nullptr},
    {const_cast<char*>("terminated"), reinterpret_cast<getter>(realm_get_terminated), nullptr,
     const_cast<char*>("Whether the last entry was stopped by its timeout."), nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

PyType_Slot realm_slots[] = {
    {Py_tp_new, reinterpret_cast<void*>(realm_new)},
    {Py_tp_dealloc, reinterpret_cast<void*>(realm_dealloc)},
    {Py_tp_traverse, reinterpret_cast<void*>(realm_traverse)},
    {Py_tp_clear, reinterpret_cast<void*>(realm_clear)},
    {Py_tp_methods, realm_methods},
    {Py_tp_getset, realm_getset},
    {Py_tp_repr, reinterpret_cast<void*>(realm_repr)},
    {Py_tp_doc, const_cast<char*>("One JavaScript global and everything installed into it.")},
    {0, nullptr},
};

PyType_Spec realm_spec = {
    "v8n.Realm",
    sizeof(RealmObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_BASETYPE,
    realm_slots,
};

}  // namespace

int
register_realm_type(PyObject* module)
{
    PyTypeObject* type = reinterpret_cast<PyTypeObject*>(PyType_FromSpec(&realm_spec));
    if (type == nullptr)
        return -1;
    if (!module::publish_type(module, "Realm", type))
    {
        Py_DECREF(reinterpret_cast<PyObject*>(type));
        return -1;
    }
    return 0;
}

}  // namespace v8n
