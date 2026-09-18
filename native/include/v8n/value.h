#ifndef V8N_VALUE_H
#define V8N_VALUE_H

// The value boundary.
//
// A value crossing out of JavaScript is exactly one of three things:
//
//   * a primitive, converted;
//   * the original Python host object, when the JavaScript object was created
//     for one;
//   * a handle, which retains the JavaScript value until the realm closes.
//
// Nothing is enumerated and no accessor is run on the way out.  That is the
// whole point: the previous binding converted eagerly, so reading one property
// of one object could run arbitrary JavaScript, and a host had no way to ask
// for a handle instead.  Materialisation is now something a caller asks for.

#include "v8n/kernel.h"
#include "v8n/v8.h"

namespace v8n
{

class Isolate;
class Realm;

// The record behind a JavaScript object that stands for a Python instance.
//
// It is doubly linked into its realm so a close can invalidate every wrapper it
// created without walking the heap, and it owns the Python reference that keeps
// the instance alive while JavaScript can still reach it.
struct PythonOwner
{
    PythonOwner* next = nullptr;
    PythonOwner* previous = nullptr;
    Realm* realm = nullptr;
    PyObject* instance = nullptr;
    // A strong record holds a Python reference.  A weak one is only an identity
    // cache entry and is dropped as soon as the instance dies.
    bool strong = true;
    // Set by the weak callback when the wrapper is collected.  The realm
    // releases the record at its next safe point rather than inside the
    // callback; see Realm::reap_owners().
    bool wants_release = false;
    // The wrapper, held weakly: a record must never be what keeps a JavaScript
    // object alive, or nothing would ever be collected.
    v8::Global<v8::Object> wrapper;
};

// How a Python-backed JavaScript value points back at its record.
//
// A private symbol, looked up by name and created by Private::ForApi, so that
// one mechanism covers both host objects and host callables.  Internal fields
// would have been the other option, but a field count is a property of a
// template, and a callable's function object is made by v8::Function::New,
// which has no template to hang a count on.  A private symbol is also invisible
// to JavaScript, which an internal field is not.
//
// A symbol is only meaningful within the isolate that made it, which is why the
// lookup takes the realm and checks the record's realm.
v8::Local<v8::Private> python_owner_key(v8::Isolate* isolate);

// True when value is a value this binding created for a Python instance.  On
// success owner points at the record and its instance stays borrowed.
bool python_owner_of(v8::Local<v8::Value> value, Realm& realm, PythonOwner** owner);

// Wrap a Python instance in a JavaScript value.
//
// A callable becomes a JavaScript function, so JavaScript can call it; anything
// else becomes a JavaScript object.  The same Python object always produces the
// same JavaScript value, so identity survives the crossing in both directions.
//
// A realm that has an interface installed for the type uses that interface's
// template, so the object carries its prototype chain.  Without one the realm's
// generic template is used, which lets a plain host object round-trip to the
// same instance even though it has no interface.
v8::Local<v8::Value> wrap_python_object(Realm& realm, PyObject* instance);

// Give an existing JavaScript object a Python identity.
//
// Construction needs this: V8 has already made the object from the interface's
// template, so it carries the prototype chain and `instanceof` works, and all
// that is missing is the Python instance behind it.  Wrapping instead would
// return a different object with the wrong prototype.
//
// Returns false with a Python error set.
bool adopt_python_object(Realm& realm, v8::Local<v8::Object> object, PyObject* instance);

// The three-state boundary described above.  Returns a new reference.
PyObject* to_python_boundary(v8::Local<v8::Value> value, Realm& realm);

// Explicit materialisation: arrays and plain objects are expanded recursively,
// cycles and shared references are preserved, and a getter that throws
// propagates.  Everything else -- functions, typed arrays, host objects --
// stays a handle.
PyObject* to_python(v8::Local<v8::Value> value, Realm& realm);

// Python to JavaScript.  Returns an empty handle with a Python error set when
// the value has no JavaScript counterpart.
v8::Local<v8::Value> to_js(PyObject* value, Realm& realm);

// The Python wrapper for a retained JavaScript value.
//
// One type covers every value; callables get a subtype so that calling is only
// offered where it means something, instead of a __call__ that fails depending
// on what the value turned out to be.
struct ValueObject
{
    PyObject_HEAD

    // Borrowed: the wrapper keeps its realm alive through a Python reference.
    Realm* realm;
    // Owned.  Null once the realm closed and the handle was released.
    HandleSlot<v8::Value>* slot;
    // A Python reference to the realm wrapper, so the realm cannot be destroyed
    // while a handle from it is still reachable.
    PyObject* realm_object;
};

// Drop the Python reference a wrapper holds.  Called from Realm::close() while
// it drains its owner records, so a finalizer never runs with a live context
// underneath it.
void release_python_owner(PythonOwner& owner);

// Discard a record without touching V8.
//
// Reached only when a realm is destroyed from a thread that cannot enter its
// isolate, which is what a Python reference count reaching zero on a worker
// thread looks like.  The Python reference is dropped, because keeping it would
// leak the host object for the life of the process.  The retained V8 handle is
// leaked on purpose: releasing a Global requires the isolate, the handle storage
// dies with the isolate anyway, and the alternative is undefined behaviour.
void discard_python_owner(PythonOwner& owner);

// Publishes v8n.Value, v8n.Function, v8n.Null and v8n.Undefined.
int register_value_types(PyObject* module);

// Build the Python wrapper for a retained JavaScript value.  Takes its own
// reference to the value; the caller keeps ownership of what it passes.
PyObject* value_wrapper(Realm& realm, v8::Local<v8::Value> value);

}  // namespace v8n

#endif
